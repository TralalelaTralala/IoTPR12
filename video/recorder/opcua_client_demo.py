"""
Демонстраційний OPC UA клієнт (замість UaExpert):
  1) показує endpoints і політики безпеки;
  2) обходить address space Plant/* і друкує значення, StatusCode та одиниці;
  3) як viewer пробує записати уставку -> BadUserAccessDenied;
  4) як operator змінює уставку температури й вмикає насос (OPC UA -> openHAB -> Modbus);
  5) читає історію температури (HistoryRead);
  6) підписка на зміни (5 с).

Запуск:  docker compose run --rm opcua-client
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from asyncua import Client, ua

URL = os.getenv("OPCUA_URL", "opc.tcp://opcua-bridge:4840/iotpr12/")
NS = "urn:iotpr12:modbus-opcua-gateway"


async def dump(client: Client) -> int:
    idx = await client.get_namespace_index(NS)
    plant = await client.nodes.objects.get_child([f"{idx}:Plant"])
    for obj in await plant.get_children():
        name = (await obj.read_browse_name()).Name
        print(f"\n  [{name}]")
        for var in await obj.get_children():
            if await var.read_node_class() != ua.NodeClass.Variable:
                continue
            dv = await var.read_data_value(raise_on_bad_status=False)
            unit = ""
            for prop in await var.get_properties():
                if (await prop.read_browse_name()).Name == "EngineeringUnits":
                    unit = (await prop.read_value()).DisplayName.Text
            bname = (await var.read_browse_name()).Name
            status = dv.StatusCode.name if dv.StatusCode else "Good"
            print(f"    {bname:<18} = {dv.Value.Value!s:<12} {unit:<5} {dv.Value.VariantType.name:<8} {status}")
    return idx


async def main() -> None:
    print(f"== Endpoints {URL}")
    async with Client(URL) as anon:
        for ep in await anon.get_endpoints():
            print(f"  {ep.SecurityPolicyUri.split('#')[-1]:<16} {ep.SecurityMode.name:<15} tokens={[t.TokenType.name for t in ep.UserIdentityTokens]}")
        print("\n== Address space (anonymous)")
        idx = await dump(anon)

    print("\n== viewer: спроба запису (очікується відмова)")
    viewer = Client(URL)
    viewer.set_user(os.getenv("OPCUA_VIEWER_USER", "viewer"))
    viewer.set_password(os.getenv("OPCUA_VIEWER_PASSWORD", "viewer123"))
    async with viewer:
        sp = viewer.get_node(ua.NodeId("Tank1.TempSetpoint", idx))
        try:
            await sp.write_value(ua.Variant(50.0, ua.VariantType.Double))
            print("  ! запис пройшов (неочікувано)")
        except ua.UaStatusCodeError as exc:
            print(f"  відмовлено: {exc.__class__.__name__}")

    print("\n== operator (Basic256Sha256 SignAndEncrypt): запис уставок")
    op = Client(URL)
    op.set_user(os.getenv("OPCUA_OPERATOR_USER", "operator"))
    op.set_password(os.getenv("OPCUA_OPERATOR_PASSWORD", "operator123"))
    try:
        from pathlib import Path

        from asyncua.crypto.cert_gen import setup_self_signed_certificate
        from cryptography.x509.oid import ExtendedKeyUsageOID

        cert, key = Path("/tmp/client_cert.der"), Path("/tmp/client_key.pem")
        op.application_uri = "urn:iotpr12:demo-client"
        if not cert.exists():
            await setup_self_signed_certificate(
                key, cert, op.application_uri, "demo-client",
                [ExtendedKeyUsageOID.CLIENT_AUTH], {"countryName": "UA", "organizationName": "IOTPR12"},
            )
        await op.set_security_string(f"Basic256Sha256,SignAndEncrypt,{cert},{key}")
    except Exception as exc:  # без клієнтського сертифіката — None endpoint
        print(f"  (secure channel unavailable: {exc}; using None)")
    async with op:
        sp = op.get_node(ua.NodeId("Tank1.TempSetpoint", idx))
        new_sp = float(sys.argv[1]) if len(sys.argv) > 1 else 65.0
        await sp.write_value(ua.Variant(new_sp, ua.VariantType.Double))
        await op.get_node(ua.NodeId("Pump1.Run", idx)).write_value(ua.Variant(True, ua.VariantType.Boolean))
        print(f"  Tank1.TempSetpoint := {new_sp}, Pump1.Run := True")
        await asyncio.sleep(4)
        print(f"  readback TempSetpoint = {await sp.read_value()}  (Modbus HR0 підтвердив)")
        print(f"  readback Pump1.Speed  = {await op.get_node(ua.NodeId('Pump1.Speed', idx)).read_value()} rpm")

        print("\n== HistoryRead Tank1.Temperature (останні 2 хв)")
        temp = op.get_node(ua.NodeId("Tank1.Temperature", idx))
        end = datetime.now(timezone.utc)
        hist = await temp.read_raw_history(end - timedelta(minutes=2), end)
        for dv in hist[-5:]:
            print(f"  {dv.SourceTimestamp:%H:%M:%S}  {dv.Value.Value:.1f} °C")
        print(f"  ... всього {len(hist)} записів")

        print("\n== Підписка на Tank1.Temperature, Pump1.Speed (5 с)")

        class Handler:
            def datachange_notification(self, node, val, data):
                print(f"  {datetime.now():%H:%M:%S} {node.nodeid.Identifier} = {val}")

        sub = await op.create_subscription(500, Handler())
        await sub.subscribe_data_change([temp, op.get_node(ua.NodeId("Pump1.Speed", idx))])
        await asyncio.sleep(5)
        await sub.delete()


if __name__ == "__main__":
    asyncio.run(main())
