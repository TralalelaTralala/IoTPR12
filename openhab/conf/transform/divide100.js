// Масштабування: сирий регістр (x100) -> інженерне значення. 182 -> "1.82"
(function (data) {
    var v = parseFloat(data);
    if (isNaN(v)) {
        return null;
    }
    return (v / 100).toFixed(2);
})(input);
