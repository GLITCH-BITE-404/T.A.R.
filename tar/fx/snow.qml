// Snow: soft flakes drifting down with a little sway.
// Flakes are small scene-graph items moved by ONE 25 fps ticker (a full-screen
// Canvas redrawn every frame was all CPU). Starts once the screen has a size.
import QtQuick

Item {
    id: snow
    readonly property bool sized: width > 0 && height > 0

    Timer {
        interval: 40; repeat: true; running: snow.sized
        onTriggered: {
            for (var i = 0; i < flakes.count; i++) {
                var f = flakes.itemAt(i);
                if (!f) continue;
                f.y += f.vy;
                f.phase += 0.05;
                f.x = f.x0 + Math.sin(f.phase) * 14;
                if (f.y > snow.height + 6) { f.y = -6; f.x0 = Math.random() * snow.width; }
            }
        }
    }

    Repeater {
        id: flakes
        model: snow.sized ? 180 : 0
        Rectangle {
            property real x0: Math.random() * snow.width
            property real vy: 0.8 + Math.random() * 2.2
            property real phase: Math.random() * 6.28
            readonly property real r: 1.5 + Math.random() * 2.5
            x: x0
            y: Math.random() * snow.height
            width: r * 2; height: r * 2; radius: r
            color: "white"
            opacity: 0.55 + Math.random() * 0.4
        }
    }
}
