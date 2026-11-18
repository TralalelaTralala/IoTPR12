// Логіка перепідключення Modbus (JS Scripting).
// Якщо після тривалої втрати зв'язку (наприклад, пристрій/контейнер зник з DNS — "Unknown host")
// Modbus binding перестає опитувати пристрій, правило перезапускає bridge-Thing (disable -> enable),
// після чого openHAB заново створює з'єднання та pollers.
//   умова: UnitN_Quality = BAD_COMM довше 30 с; повтор не частіше ніж раз на 30 с.

const UNITS = [
  { quality: 'Unit1_Quality', bridge: 'modbus:tcp:unit1' },
  { quality: 'Unit2_Quality', bridge: 'modbus:tcp:unit2' },
];
const BAD_FOR_MS = 30000;

rules.JSRule({
  name: "Modbus: перепідключення після втрати зв'язку",
  description: 'Перезапуск modbus:tcp bridge, якщо якість BAD_COMM > 30 с',
  triggers: [triggers.GenericCronTrigger('0/10 * * * * ?')],
  execute: () => {
    const now = Date.now();
    for (const u of UNITS) {
      const key = 'badSince_' + u.bridge;
      if (items.getItem(u.quality).state !== 'BAD_COMM') {
        cache.private.remove(key);
        continue;
      }
      const since = cache.private.get(key, () => now);
      if (now - since < BAD_FOR_MS) {
        continue;
      }
      const thing = things.getThing(u.bridge);
      console.warn(`${u.quality} = BAD_COMM > ${BAD_FOR_MS / 1000} s -> restarting ${u.bridge}`);
      thing.setEnabled(false);
      setTimeout(() => thing.setEnabled(true), 2000);
      cache.private.put(key, now); // наступна спроба — не раніше ніж через 30 с
    }
  },
});
