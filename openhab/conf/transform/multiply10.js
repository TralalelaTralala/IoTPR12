// Зворотне масштабування для запису: 60.5 -> "605" (ціле для int16-регістра).
// Значення обмежується діапазоном int16, щоб некоректна команда не переповнила регістр.
(function (data) {
    var v = parseFloat(String(data).replace(",", "."));
    if (isNaN(v)) {
        return null;
    }
    var raw = Math.round(v * 10);
    return String(Math.max(-32768, Math.min(32767, raw)));
})(input);
