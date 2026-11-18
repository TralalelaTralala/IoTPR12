// Масштабування: сирий регістр (x10) -> інженерне значення. 235 -> "23.5"
(function (data) {
    var v = parseFloat(data);
    if (isNaN(v)) {
        return null;
    }
    return (v / 10).toFixed(1);
})(input);
