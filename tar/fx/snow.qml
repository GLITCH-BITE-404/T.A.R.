// Snow: soft flakes drifting down with a little sway.
import QtQuick

Canvas {
    id: c
    property var flakes: []
    renderStrategy: Canvas.Threaded

    function make() {
        var f = [];
        for (var i = 0; i < 220; i++)
            f.push({ x: Math.random() * width, y: Math.random() * height,
                     r: 1 + Math.random() * 3, v: 0.6 + Math.random() * 1.6,
                     p: Math.random() * 6.28 });
        flakes = f;
    }
    onWidthChanged: make()

    onPaint: {
        var ctx = getContext("2d");
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = "rgba(255,255,255,0.85)";
        for (var i = 0; i < flakes.length; i++) {
            var f = flakes[i];
            f.y += f.v; f.p += 0.02;
            var x = f.x + Math.sin(f.p) * 12;
            if (f.y > height + 5) { f.y = -5; f.x = Math.random() * width; }
            ctx.beginPath();
            ctx.arc(x, f.y, f.r, 0, 6.283);
            ctx.fill();
        }
    }
    Timer { interval: 33; running: true; repeat: true; onTriggered: c.requestPaint() }
}
