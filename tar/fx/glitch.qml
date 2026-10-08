// Glitch: random RGB-split bars and scanline flicker tearing across the screen.
import QtQuick

Item {
    id: g
    Repeater {
        model: 14
        Rectangle {
            id: bar
            width: g.width
            x: 0
            color: ["#ff0040", "#00ffd0", "#ffffff", "#7a00ff"][index % 4]
            opacity: 0
            SequentialAnimation {
                running: true
                loops: Animation.Infinite
                PauseAnimation { duration: 80 + Math.random() * 900 }
                ScriptAction { script: { bar.y = Math.random() * g.height;
                                         bar.height = 2 + Math.random() * 40;
                                         bar.x = (Math.random() - 0.5) * 60; } }
                NumberAnimation { target: bar; property: "opacity"; to: 0.25 + Math.random() * 0.35; duration: 30 }
                PauseAnimation { duration: 40 + Math.random() * 120 }
                NumberAnimation { target: bar; property: "opacity"; to: 0; duration: 60 }
            }
        }
    }
    // faint scanlines over everything
    Column {
        anchors.fill: parent
        Repeater {
            model: Math.ceil(g.height / 4)
            Rectangle { width: g.width; height: 1; color: "#000000"; opacity: 0.18
                        Rectangle { y: 1; width: parent.width; height: 3; color: "transparent" } }
        }
        spacing: 3
    }
}
