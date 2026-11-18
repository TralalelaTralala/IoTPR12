"""
Запис демо-відео IOTPR12 (Playwright, headless Chromium, 1280x720).

Сцени: титул -> архітектура -> карта регістрів -> SCADA (огляд, керування, діагностика)
-> OPC UA клієнт наживо -> аварії -> авто-режим -> тренди -> втрата зв'язку -> відновлення -> фінал.

Таймкоди сцен пишуться у /out/scenes.json (під них складається текст озвучки).
Зупинку/запуск Modbus-емулятора виконує хост: скрипт пише /out/signal (stop/start),
хостовий цикл (video/record.sh) виконує docker stop/start.
"""
import asyncio
import json
import os
import time
from pathlib import Path

from playwright.async_api import async_playwright

OH = os.getenv("OH", "http://openhab:8080")
UI = f"{OH}/basicui/app?sitemap=scada"
OUT = Path("/out")
W, H = 1280, 720

T0 = 0.0
SCENES = []


def mark(name: str) -> None:
    t = time.monotonic() - T0
    SCENES.append({"t": round(t, 1), "scene": name})
    print(f"[{t:6.1f}] {name}", flush=True)


async def signal(cmd: str) -> None:
    (OUT / "signal").write_text(cmd)
    for _ in range(100):
        await asyncio.sleep(0.2)
        if not (OUT / "signal").exists():
            return
    print("signal not consumed:", cmd)


CSS = """
<style>
 body{margin:0;width:1280px;height:720px;background:#0f172a;color:#e2e8f0;font-family:'Segoe UI',Roboto,Arial,sans-serif;display:flex;flex-direction:column;justify-content:center;align-items:center;overflow:hidden}
 h1{font-size:48px;margin:0 0 12px;color:#fff} h2{font-size:30px;margin:0 0 26px;color:#93c5fd;font-weight:500}
 .sub{font-size:22px;color:#94a3b8;margin-top:6px}
 .row{display:flex;gap:12px;align-items:center;max-width:1220px}
 .box{border:2px solid #334155;border-radius:14px;padding:14px 16px;background:#1e293b;min-width:150px;text-align:center;font-size:17px;line-height:1.45}
 .box b{display:block;font-size:22px;color:#fff;margin-bottom:4px}
 .mb{border-color:#f59e0b}.oh{border-color:#22c55e}.ua{border-color:#3b82f6}.cl{border-color:#a855f7}
 .arr{font-size:34px;color:#64748b}
 table{border-collapse:collapse;font-size:17px;background:#1e293b;border-radius:10px;overflow:hidden}
 th,td{padding:7px 14px;border-bottom:1px solid #334155;text-align:left} th{background:#334155;color:#fff}
 td.c{color:#fbbf24;font-family:Consolas,monospace}
 .term{width:1180px;height:640px;background:#020617;border:1px solid #334155;border-radius:10px;padding:18px 22px;box-sizing:border-box;font-family:Consolas,'Cascadia Mono',monospace;font-size:15px;line-height:1.35;color:#cbd5e1;white-space:pre;overflow:hidden;align-self:center}
 .term .p{color:#22c55e}.term .h{color:#93c5fd;font-weight:bold}.term .e{color:#f87171}.term .g{color:#86efac}
</style>"""

TITLE = CSS + """
<h1>Практична робота 12.1 — варіант 5</h1>
<h2>Industrial IoT Gateway: Modbus RTU/TCP + OPC UA через openHAB</h2>
<div class="sub">Modbus TCP · Modbus RTU over TCP · openHAB 4.2 · OPC UA (asyncua) · RRD4j · SCADA sitemap</div>
"""

ARCH = CSS + """
<h2>Архітектура шлюзу</h2>
<div class="row">
  <div><div class="box mb"><b>Unit 1 — резервуар</b>Modbus TCP<br>:5020, slave 1</div><br>
       <div class="box mb"><b>Unit 2 — насос</b>Modbus RTU over TCP<br>:5021, slave 2</div></div>
  <div class="arr">⇄</div>
  <div class="box oh" style="min-width:290px"><b>openHAB 4.2</b>Modbus binding (FC01–06, 15, 16)<br>JS-масштабування, MAP<br>Rules: якість, аварії, авто, історія<br>RRD4j · Sitemap SCADA · REST API</div>
  <div class="arr">⇄</div>
  <div class="box ua"><b>OPC UA сервер</b>opc.tcp://:4840<br>Plant / Tank1, Pump1,<br>Alarms, Gateway<br>Basic256Sha256</div>
  <div class="arr">⇄</div>
  <div class="box cl"><b>Клієнти</b>UaExpert<br>SCADA<br>opcua-client</div>
</div>
"""

REGMAP = CSS + """
<h2>Modbus register map → OPC UA nodes</h2>
<table>
<tr><th>Unit</th><th>Область</th><th>Адреса</th><th>Тип / масштаб</th><th>Значення</th><th>OPC UA вузол</th></tr>
<tr><td>1 TCP</td><td>Input Reg (FC04)</td><td class=c>IR0</td><td>int16 ×0.1</td><td>Температура, °C</td><td class=c>Tank1.Temperature</td></tr>
<tr><td>1 TCP</td><td>Input Reg</td><td class=c>IR4–5</td><td>float32</td><td>Енергія, kWh</td><td class=c>Tank1.HeaterEnergy</td></tr>
<tr><td>1 TCP</td><td>Holding (FC03/06/16)</td><td class=c>HR0</td><td>int16 ×0.1</td><td>Уставка, °C</td><td class=c>Tank1.TempSetpoint</td></tr>
<tr><td>1 TCP</td><td>Coil (FC01/05/15)</td><td class=c>C0</td><td>bit</td><td>Нагрівач</td><td class=c>Tank1.HeaterOn</td></tr>
<tr><td>1 TCP</td><td>Discrete In (FC02)</td><td class=c>DI2</td><td>bit</td><td>Термостат перегріву</td><td class=c>Tank1.OvertempSwitch</td></tr>
<tr><td>2 RTU</td><td>Input Reg</td><td class=c>IR1</td><td>uint16 ×0.01</td><td>Струм, A</td><td class=c>Pump1.MotorCurrent</td></tr>
<tr><td>2 RTU</td><td>Input Reg</td><td class=c>IR4–5</td><td>uint32</td><td>Напрацювання, с</td><td class=c>Pump1.RunTime</td></tr>
<tr><td>2 RTU</td><td>Coil</td><td class=c>C0</td><td>bit</td><td>Пуск насоса</td><td class=c>Pump1.Run</td></tr>
<tr><td>2 RTU</td><td>Discrete In</td><td class=c>DI1</td><td>bit</td><td>Аварія частотника</td><td class=c>Pump1.Fault</td></tr>
</table>
<div class="sub" style="margin-top:18px">Якість: GOOD → Good · UNCERTAIN_STALE → UncertainLastUsableValue · BAD_COMM → BadCommunicationError</div>
"""

FINAL = CSS + """
<h1>Підсумок</h1>
<div class="sub" style="font-size:24px;line-height:1.7;text-align:center">
2 Modbus-пристрої (TCP та RTU) · 35 вузлів OPC UA з одиницями та кодами якості<br>
ролі operator / viewer · Basic256Sha256 · HistoryRead<br>
аварії з квитуванням · авто-режим · тренди RRD4j · автоматичне перепідключення
</div>
"""

HIGHLIGHT_JS = """
(label) => {
  document.querySelectorAll('.demo-hl').forEach(e => { e.classList.remove('demo-hl'); e.style.outline=''; e.style.background=''; });
  const leaf = [...document.querySelectorAll('.mdl-form__label, .mdl-form__title')]
      .find(e => e.textContent.trim().startsWith(label));
  if (!leaf) return false;
  const el = leaf.closest('.mdl-form__row') || leaf.closest('.mdl-form') || leaf;
  el.classList.add('demo-hl');
  el.style.outline = '3px solid #f59e0b'; el.style.background = '#fff7e6';
  el.scrollIntoView({block: 'center', behavior: 'smooth'});
  return true;
}
"""

FRAME_JS = """
(title) => {
  const t = [...document.querySelectorAll('.mdl-form__title')].find(e => e.textContent.trim().startsWith(title));
  if (!t) return false;
  t.scrollIntoView({block: 'start', behavior: 'smooth'});
  window.scrollBy(0, -70);
  return true;
}
"""


class Demo:
    def __init__(self, page):
        self.page = page

    async def cmd(self, item: str, value: str) -> None:
        r = await self.page.request.post(f"{OH}/rest/items/{item}", data=value, headers={"Content-Type": "text/plain"})
        print(f"   cmd {item} := {value} -> {r.status}", flush=True)

    async def state(self, item: str) -> str:
        r = await self.page.request.get(f"{OH}/rest/items/{item}/state")
        return await r.text()

    async def hl(self, label: str, wait: float = 0.0) -> None:
        ok = await self.page.evaluate(HIGHLIGHT_JS, label)
        if not ok:
            print("   highlight not found:", label, flush=True)
        await asyncio.sleep(wait)

    async def frame(self, title: str, wait: float = 0.0) -> None:
        await self.page.evaluate(FRAME_JS, title)
        await asyncio.sleep(wait)

    async def slide(self, html: str, wait: float) -> None:
        await self.page.set_content(html)
        await asyncio.sleep(wait)

    async def ui(self) -> None:
        await self.page.goto(UI, wait_until="load")
        await self.page.wait_for_selector(".mdl-form__label", timeout=20000)
        await asyncio.sleep(1.5)

    async def wait_for(self, item: str, value: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if (await self.state(item)) == value:
                return True
            await asyncio.sleep(0.5)
        print(f"   timeout waiting {item}={value}", flush=True)
        return False

    async def terminal(self) -> None:
        """Запускає справжній OPC UA клієнт і показує його вивід як термінал."""
        await self.page.set_content(CSS + '<div class="term" id="t"></div>')
        add = """([cls, text]) => { const t = document.getElementById('t'); const s = document.createElement('span');
                 if (cls) s.className = cls; s.textContent = text + '\\n'; t.appendChild(s);
                 while (t.scrollHeight > t.clientHeight && t.firstChild) t.removeChild(t.firstChild); }"""
        await self.page.evaluate(add, ["p", "$ docker compose run --rm opcua-client"])
        proc = await asyncio.create_subprocess_exec(
            "python3", "-u", "/app/opcua_client_demo.py", "62",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "OPCUA_URL": "opc.tcp://opcua-bridge:4840/iotpr12/"},
        )
        section, hide = "", False
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            if not text.strip():
                continue
            if text.startswith("== "):
                section, hide = text, False
            elif "Address space" in section and text.strip().startswith("["):
                hide = "Tank1" not in text  # повний обхід довгий — у кадрі лише Tank1
            if hide:
                continue
            if "Address space" in text:
                mark("opcua_tree")
            elif "viewer" in text:
                mark("opcua_viewer")
            elif "operator" in text:
                mark("opcua_operator")
            elif "HistoryRead" in text:
                mark("opcua_history")
            elif "Підписка" in text:
                mark("opcua_subscription")
            cls = "h" if text.startswith("==") else ("e" if "відмовлено" in text else ("g" if "readback" in text else ""))
            await self.page.evaluate(add, [cls, text])
            await asyncio.sleep(0.32)
        await proc.wait()
        await asyncio.sleep(5.0)


async def main() -> None:
    global T0
    OUT.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT), record_video_size={"width": W, "height": H}, locale="uk-UA")
        page = await ctx.new_page()
        T0 = time.monotonic()
        d = Demo(page)

        mark("architecture")
        await d.slide(ARCH, 21)
        mark("register_map")
        await d.slide(REGMAP, 13)

        mark("ui_overview")
        await d.ui()
        await d.hl("Температура води", 3.5)
        await d.hl("Рівень у резервуарі", 2.5)
        await d.hl("Якість даних Unit 1", 3.5)
        await d.hl("Якість даних Unit 2", 2.5)

        mark("manual_control")
        await d.frame("Керування", 1.5)
        await d.cmd("Tank_InletValve", "OFF")
        await d.cmd("Pump_SpeedSetpoint", "100")
        await d.hl("Уставка швидкості", 2.5)
        await d.cmd("Pump_Run", "ON")
        await d.hl("Пуск насоса", 3.0)
        await d.frame("Огляд процесу", 0.5)
        await d.hl("Швидкість двигуна", 4.0)
        await d.hl("Струм двигуна", 3.0)
        await d.hl("Рівень у резервуарі", 3.0)

        mark("diagnostics")
        await page.get_by_text("Unit 2 — насос (Modbus RTU over TCP, id 2)").click()
        await page.wait_for_selector("text=Input Registers (FC04)", timeout=15000)
        await asyncio.sleep(1.0)
        await d.hl("Швидкість двигуна", 2.5)
        await d.hl("Напрацювання", 3.0)
        await d.hl("Unit 2: останнє читання", 3.0)
        await page.go_back(wait_until="load")
        await asyncio.sleep(1.5)

        mark("opcua_client")
        await d.terminal()

        mark("opcua_result_in_ui")
        await d.ui()
        await d.frame("Керування", 0.5)
        await d.hl("Уставка температури", 5.0)

        mark("alarms")
        await d.wait_for("Pump_Fault", "ON", 60)
        await d.frame("Аварії", 1.0)
        await d.hl("Низький рівень", 3.0)
        await d.hl("Аварія насоса", 3.0)
        await d.hl("Журнал", 4.0)

        mark("ack_and_auto")
        await d.cmd("Pump_Run", "OFF")
        await d.cmd("Pump_FaultReset", "ON")
        await d.cmd("Alarm_Ack", "ON")
        await d.hl("Неквитовані аварії", 3.0)
        await d.cmd("Auto_Mode", "ON")
        await d.frame("Керування", 0.5)
        await d.hl("Автоматичний режим", 3.5)
        await d.hl("Впускний клапан (авто)", 3.5)
        await d.hl("Нагрівач (авто)", 3.0)

        mark("trends")
        await d.frame("Тренди", 7.0)
        await d.frame("Історія: агрегати", 5.0)

        mark("comm_loss")
        await signal("stop")
        await d.frame("Огляд процесу", 0.5)
        await d.hl("Якість даних Unit 1", 0.5)
        await d.wait_for("Unit1_Quality", "UNCERTAIN_STALE", 20)
        mark("comm_uncertain")
        await asyncio.sleep(3)
        await d.wait_for("Unit1_Quality", "BAD_COMM", 40)
        mark("comm_bad")
        await d.hl("Якість даних Unit 2", 2.0)
        await d.frame("Аварії", 0.5)
        await d.hl("Немає зв'язку з Unit 1", 4.0)

        mark("comm_restore")
        await signal("start")
        await d.frame("Огляд процесу", 0.5)
        await d.hl("Якість даних Unit 1", 0.5)
        await d.wait_for("Unit1_Quality", "GOOD", 40)
        mark("comm_good")
        await asyncio.sleep(4)

        mark("final")
        await d.slide(FINAL, 9)
        mark("end")

        (OUT / "scenes.json").write_text(json.dumps(SCENES, ensure_ascii=False, indent=1))
        video = page.video
        await ctx.close()
        await browser.close()
        src = await video.path()
        Path(src).rename(OUT / "IOTPR12-demo.webm")
        print("video:", OUT / "IOTPR12-demo.webm")


if __name__ == "__main__":
    asyncio.run(main())
