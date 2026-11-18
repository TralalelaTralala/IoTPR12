"""
OPC UA сервер-шлюз: публікує дані Modbus-пристроїв, які опитує openHAB, як вузли OPC UA.

    Modbus slave --(Modbus binding)--> openHAB items --(REST API)--> цей сервер --(opc.tcp)--> SCADA / UaExpert

* Address space будується з mapping.json: Objects/Plant/<Object>/<Variable>, NodeId ns=2;s=<Object>.<Variable>.
* Типи даних: openHAB-рядки -> Double / UInt16 / UInt32 / Boolean / String (Variant).
* Інженерні одиниці: властивості EngineeringUnits (UNECE-коди) та EURange.
* Коди якості: Unit*_Quality з openHAB -> StatusCode (Good / Uncertain / Bad).
* Запис від OPC UA клієнта у вузли з access=rw пересилається в openHAB як команда -> запис у Modbus.
* Безпека: None + Basic256Sha256 (Sign, SignAndEncrypt), користувачі operator (запис) / viewer (читання).
* Історія: вузли з historize=true зберігають значення в пам'яті, доступні через HistoryRead.
"""
import asyncio
import json
import logging
import math
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from asyncua import Server, ua
from asyncua.crypto.permission_rules import PermissionRuleset
from asyncua.server.users import User, UserRole

log = logging.getLogger("opcua-bridge")

OH_URL = os.getenv("OPENHAB_URL", "http://openhab:8080").rstrip("/")
OH_TOKEN = os.getenv("OPENHAB_TOKEN", "")
ENDPOINT = os.getenv("OPCUA_ENDPOINT", "opc.tcp://0.0.0.0:4840/iotpr12/")
PERIOD = float(os.getenv("SYNC_PERIOD", "1.0"))
MAPPING = Path(os.getenv("MAPPING_FILE", Path(__file__).with_name("mapping.json")))
CERT_DIR = Path(os.getenv("CERT_DIR", "/certs"))
ALLOW_ANONYMOUS = os.getenv("OPCUA_ALLOW_ANONYMOUS", "true").lower() == "true"
USERS = {
    os.getenv("OPCUA_OPERATOR_USER", "operator"): (os.getenv("OPCUA_OPERATOR_PASSWORD", "operator123"), UserRole.Admin),
    os.getenv("OPCUA_VIEWER_USER", "viewer"): (os.getenv("OPCUA_VIEWER_PASSWORD", "viewer123"), UserRole.User),
}
APP_URI = "urn:iotpr12:opcua-bridge"

VARIANT = {
    "Double": ua.VariantType.Double,
    "UInt16": ua.VariantType.UInt16,
    "UInt32": ua.VariantType.UInt32,
    "Boolean": ua.VariantType.Boolean,
    "String": ua.VariantType.String,
}
DEFAULT = {"Double": 0.0, "UInt16": 0, "UInt32": 0, "Boolean": False, "String": ""}

# Quality codes: рядок з openHAB -> OPC UA StatusCode
QUALITY = {
    "GOOD": ua.StatusCodes.Good,
    "UNCERTAIN_STALE": ua.StatusCodes.UncertainLastUsableValue,
    "BAD_COMM": ua.StatusCodes.BadCommunicationError,
}


# ---------------------------------------------------------------- безпека
class GatewayUserManager:
    """operator -> Admin (читання + запис), viewer -> User (тільки читання), anonymous -> User (опційно)."""

    def get_user(self, iserver, username=None, password=None, certificate=None):
        if username is None:
            if ALLOW_ANONYMOUS:
                return User(role=UserRole.User, name="anonymous")
            log.warning("anonymous login rejected")
            return None
        entry = USERS.get(username)
        if entry and entry[0] == password:
            log.info("login: %s (%s)", username, entry[1].name)
            return User(role=entry[1], name=username)
        log.warning("login rejected for user %r", username)
        return None


class GatewayRuleset(PermissionRuleset):
    """Запити, що змінюють дані (Write, Call, Add/Delete Nodes/References), дозволені лише ролі Admin."""

    MODIFY = {
        ua.NodeId(getattr(ua.ObjectIds, name))
        for name in (
            "WriteRequest_Encoding_DefaultBinary",
            "CallRequest_Encoding_DefaultBinary",
            "AddNodesRequest_Encoding_DefaultBinary",
            "DeleteNodesRequest_Encoding_DefaultBinary",
            "AddReferencesRequest_Encoding_DefaultBinary",
            "DeleteReferencesRequest_Encoding_DefaultBinary",
            "HistoryUpdateRequest_Encoding_DefaultBinary",
        )
    }

    def check_validity(self, user, action_type_id, body):
        if action_type_id in self.MODIFY:
            allowed = user is not None and user.role == UserRole.Admin
            if not allowed:
                log.warning("write denied for %s", getattr(user, "name", None))
            return allowed
        return True


# ---------------------------------------------------------------- openHAB REST
class OpenHAB:
    def __init__(self, base: str, token: str = "", timeout: float = 3.0):
        self.base, self.token, self.timeout = base, token, timeout

    def _request(self, method: str, path: str, body: str | None = None) -> str:
        data = body.encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "text/plain")
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8")

    async def states(self) -> dict[str, str]:
        raw = await asyncio.to_thread(self._request, "GET", "/rest/items?recursive=false&fields=name,state")
        return {i["name"]: i.get("state", "NULL") for i in json.loads(raw)}

    async def command(self, item: str, value: str) -> None:
        await asyncio.to_thread(self._request, "POST", "/rest/items/" + urllib.parse.quote(item), value)

    async def update(self, item: str, value: str) -> None:
        await asyncio.to_thread(self._request, "PUT", "/rest/items/" + urllib.parse.quote(item) + "/state", value)


# ---------------------------------------------------------------- перетворення типів
def oh_to_ua(state: str | None, typ: str):
    """Рядок стану openHAB -> значення Python потрібного OPC UA типу (None, якщо NULL/UNDEF/помилка)."""
    if state is None or state in ("NULL", "UNDEF"):
        return None
    try:
        if typ == "Boolean":
            return state.upper() in ("ON", "OPEN", "1", "TRUE")
        if typ == "String":
            return state
        num = float(state.split(" ")[0])  # "23.5 °C" -> 23.5
        if typ == "Double":
            return num
        if typ == "UInt16":
            return max(0, min(0xFFFF, int(round(num))))
        if typ == "UInt32":
            return max(0, min(0xFFFFFFFF, int(round(num))))
    except (ValueError, AttributeError):
        log.warning("cannot convert %r to %s", state, typ)
    return None


def ua_to_oh(value, var: dict) -> str | None:
    """Значення, записане клієнтом OPC UA -> команда openHAB (з валідацією діапазону)."""
    typ = var["type"]
    if typ == "Boolean":
        return "ON" if bool(value) else "OFF"
    if typ == "String":
        return str(value)[:200]
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(num) or math.isinf(num):
        return None
    lo, hi = var.get("range", [-math.inf, math.inf])
    if not lo <= num <= hi:
        return None
    return str(int(num)) if typ.startswith("UInt") else f"{num:.2f}"


def unece_id(code: str) -> int:
    """UNECE Rec.20 код ('CEL') -> UnitId для EUInformation (OPC UA Part 8)."""
    uid = 0
    for ch in code.upper()[:4]:
        uid = (uid << 8) | ord(ch)
    return uid


# ---------------------------------------------------------------- шлюз
class Gateway:
    def __init__(self):
        self.server = Server(user_manager=GatewayUserManager())
        self.oh = OpenHAB(OH_URL, OH_TOKEN)
        self.vars: list[dict] = []         # елементи mapping + node + quality_item
        self.last_pushed: dict[str, object] = {}
        self.pending: dict[str, tuple[object, float]] = {}
        self.writes = 0
        self.connected = False
        self.gw_nodes: dict[str, object] = {}

    async def setup(self) -> None:
        cfg = json.loads(MAPPING.read_text(encoding="utf-8"))
        await self.server.init()
        self.server.set_endpoint(ENDPOINT)
        self.server.set_server_name("IOTPR12 Modbus-OPC UA Gateway (openHAB)")
        await self.server.set_application_uri(APP_URI)
        self.server.set_security_policy(
            [
                ua.SecurityPolicyType.NoSecurity,
                ua.SecurityPolicyType.Basic256Sha256_Sign,
                ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt,
            ],
            permission_ruleset=GatewayRuleset(),
        )
        self.server.set_security_IDs(["Anonymous", "Username", "Basic256Sha256"] if ALLOW_ANONYMOUS else ["Username", "Basic256Sha256"])
        await self.load_certificates()

        idx = await self.server.register_namespace(cfg["namespace"])
        root = await self.server.nodes.objects.add_folder(ua.NodeId(cfg["root"], idx), ua.QualifiedName(cfg["root"], idx))

        for obj in cfg["objects"]:
            onode = await root.add_object(ua.NodeId(obj["name"], idx), ua.QualifiedName(obj["name"], idx))
            await onode.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(obj.get("description", "")))))
            for var in obj["variables"]:
                typ = var["type"]
                nid = ua.NodeId(f"{obj['name']}.{var['name']}", idx)
                node = await onode.add_variable(nid, ua.QualifiedName(var["name"], idx), DEFAULT[typ], varianttype=VARIANT[typ])
                if var.get("access") == "rw":
                    await node.set_writable()
                desc = var.get("modbus", "")
                if desc:
                    await node.write_attribute(ua.AttributeIds.Description, ua.DataValue(ua.Variant(ua.LocalizedText(f"Modbus {desc}; openHAB item {var['item']}"))))
                await self.add_eu(node, idx, var)
                self.vars.append({**var, "node": node, "key": f"{obj['name']}.{var['name']}", "quality_item": obj.get("quality_item")})
                self.last_pushed[f"{obj['name']}.{var['name']}"] = DEFAULT[typ]

        # діагностика шлюзу
        gw = await root.add_object(ua.NodeId("Gateway", idx), ua.QualifiedName("Gateway", idx))
        for name, val, vt in (
            ("OpenHabConnected", False, ua.VariantType.Boolean),
            ("LastSync", datetime.now(timezone.utc), ua.VariantType.DateTime),
            ("WritesForwarded", 0, ua.VariantType.UInt32),
            ("ConnectedClients", 0, ua.VariantType.UInt32),
            ("Subscriptions", 0, ua.VariantType.UInt32),
        ):
            self.gw_nodes[name] = await gw.add_variable(ua.NodeId(f"Gateway.{name}", idx), ua.QualifiedName(name, idx), val, varianttype=vt)
        log.info("address space: %d variables from %s", len(self.vars), MAPPING.name)

    async def add_eu(self, node, idx: int, var: dict) -> None:
        if "eu" not in var:
            return
        try:
            eu = ua.EUInformation(
                NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact",
                UnitId=unece_id(var["eu"]),
                DisplayName=ua.LocalizedText(var.get("eu_name", var["eu"])),
                Description=ua.LocalizedText(var.get("eu_name", var["eu"])),
            )
            await node.add_property(idx, ua.QualifiedName("EngineeringUnits", 0), eu, datatype=ua.NodeId(ua.ObjectIds.EUInformation))
            if "range" in var:
                rng = ua.Range(Low=float(var["range"][0]), High=float(var["range"][1]))
                await node.add_property(idx, ua.QualifiedName("EURange", 0), rng, datatype=ua.NodeId(ua.ObjectIds.Range))
        except Exception as exc:  # властивості необов'язкові
            log.debug("EU properties for %s skipped: %s", var["name"], exc)

    async def load_certificates(self) -> None:
        CERT_DIR.mkdir(parents=True, exist_ok=True)
        cert, key = CERT_DIR / "server_cert.der", CERT_DIR / "server_key.pem"
        if not cert.exists() or not key.exists():
            from asyncua.crypto.cert_gen import setup_self_signed_certificate
            from cryptography.x509.oid import ExtendedKeyUsageOID

            await setup_self_signed_certificate(
                key, cert, APP_URI, socket.gethostname(),
                [ExtendedKeyUsageOID.CLIENT_AUTH, ExtendedKeyUsageOID.SERVER_AUTH],
                {"countryName": "UA", "organizationName": "IOTPR12", "localityName": "Kyiv"},
            )
            log.info("generated self-signed certificate %s", cert)
        await self.server.load_certificate(str(cert))
        await self.server.load_private_key(str(key))

    # ------------------------------------------------------------ синхронізація
    async def write_node(self, v: dict, value, status: int) -> None:
        if value is None:
            value = self.last_pushed[v["key"]]
        now = datetime.now(timezone.utc)
        dv = ua.DataValue(
            Value=ua.Variant(value, VARIANT[v["type"]]),
            StatusCode_=ua.StatusCode(status),
            SourceTimestamp=now,
            ServerTimestamp=now,
        )
        await self.server.write_attribute_value(v["node"].nodeid, dv)
        self.last_pushed[v["key"]] = value

    async def forward_client_writes(self) -> None:
        """Знаходить значення, змінені OPC UA клієнтами, і надсилає їх у openHAB (-> Modbus)."""
        for v in self.vars:
            if v.get("access") != "rw":
                continue
            dv = await v["node"].read_data_value(raise_on_bad_status=False)
            current = dv.Value.Value if dv.Value is not None else None
            # вузол без значення (Bad-статус) — це не запис клієнта
            if current is None or current == self.last_pushed[v["key"]]:
                continue
            cmd = ua_to_oh(current, v)
            if cmd is None:
                log.warning("rejected OPC UA write %s=%r (type/range)", v["key"], current)
                await self.write_node(v, self.last_pushed[v["key"]], ua.StatusCodes.BadOutOfRange)
                continue
            await self.oh.command(v["item"], cmd)
            self.writes += 1
            self.last_pushed[v["key"]] = current
            self.pending[v["key"]] = (current, asyncio.get_running_loop().time())
            log.info("OPC UA write %s=%r -> openHAB %s %s -> Modbus %s", v["key"], current, v["item"], cmd, v.get("modbus", "-"))

    async def apply_states(self, states: dict[str, str]) -> None:
        loop_t = asyncio.get_running_loop().time()
        for v in self.vars:
            value = oh_to_ua(states.get(v["item"]), v["type"])
            # поки Modbus-пристрій не підтвердив запис (до 3 с), не перетираємо значення клієнта
            pend = self.pending.get(v["key"])
            if pend is not None:
                if value == pend[0] or loop_t - pend[1] > 3.0:
                    self.pending.pop(v["key"], None)
                else:
                    continue
            q = states.get(v["quality_item"]) if v["quality_item"] else "GOOD"
            if value is None:
                status = ua.StatusCodes.BadWaitingForInitialData
            else:
                status = QUALITY.get(q, ua.StatusCodes.UncertainInitialValue)
            if value != self.last_pushed[v["key"]] or status != v.get("_status"):
                await self.write_node(v, value, status)
                v["_status"] = status

    async def mark_disconnected(self) -> None:
        for v in self.vars:
            if v.get("_status") != ua.StatusCodes.BadNotConnected:
                await self.write_node(v, None, ua.StatusCodes.BadNotConnected)
                v["_status"] = ua.StatusCodes.BadNotConnected

    def clients(self) -> int:
        bserver = getattr(self.server, "bserver", None)
        return len(getattr(bserver, "clients", []) or []) if bserver else 0

    def subscriptions(self) -> int:
        svc = getattr(self.server.iserver, "subscription_service", None)
        return len(getattr(svc, "subscriptions", {}) or {}) if svc else 0

    async def report_status(self) -> None:
        g = self.gw_nodes
        await g["OpenHabConnected"].write_value(self.connected)
        await g["LastSync"].write_value(datetime.now(timezone.utc))
        await g["WritesForwarded"].write_value(ua.Variant(self.writes, ua.VariantType.UInt32))
        await g["ConnectedClients"].write_value(ua.Variant(self.clients(), ua.VariantType.UInt32))
        await g["Subscriptions"].write_value(ua.Variant(self.subscriptions(), ua.VariantType.UInt32))
        if self.connected:
            try:
                await self.oh.update("OPCUA_Status", "RUNNING " + ENDPOINT.replace("0.0.0.0", "localhost"))
                await self.oh.update("OPCUA_Clients", str(self.clients()))
                await self.oh.update("OPCUA_Subscriptions", str(self.subscriptions()))
                await self.oh.update("OPCUA_Writes", str(self.writes))
                await self.oh.update("OPCUA_Heartbeat", datetime.now().astimezone().isoformat(timespec="seconds"))
            except (urllib.error.URLError, OSError) as exc:
                log.debug("status report failed: %s", exc)

    async def run(self) -> None:
        await self.setup()
        async with self.server:
            for v in self.vars:
                if v.get("historize"):
                    await self.server.historize_node_data_change(v["node"], period=timedelta(hours=1), count=3600)
            log.info("OPC UA server listening on %s (openHAB: %s)", ENDPOINT, OH_URL)
            backoff, tick, last_clients = 1.0, 0, -1
            while True:
                try:
                    if self.connected:
                        await self.forward_client_writes()
                    states = await self.oh.states()
                    await self.apply_states(states)
                    if not self.connected:
                        log.info("connected to openHAB REST API")
                    self.connected, backoff = True, 1.0
                except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
                    if self.connected or backoff == 1.0:
                        log.warning("openHAB unavailable (%s), retry in %.0f s", exc, backoff)
                    self.connected = False
                    await self.mark_disconnected()
                    await self.report_status()
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)  # експоненційна затримка перепідключення
                    continue
                tick += 1
                if tick % 5 == 0:
                    await self.report_status()
                    if self.clients() != last_clients:
                        last_clients = self.clients()
                        log.info("OPC UA clients connected: %d, subscriptions: %d", last_clients, self.subscriptions())
                await asyncio.sleep(PERIOD)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("asyncua", "asyncua.server.address_space", "asyncua.server.uaprocessor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(Gateway().run())
