// Written by T.A.R. itself (make_fx) on 2026-10-08, kept as a built-in.
import QtQuick

Item {
    id: root
    width: parent ? parent.width : 1920
    height: parent ? parent.height : 1080

    property real time: 0
    Timer {
        interval: 30
        running: true
        repeat: true
        onTriggered: root.time += 0.03
    }

    Repeater {
        model: 8
        delegate: Item {
            property real cx: (0.15 + index * 0.1) * root.width
            property real cy: root.height * (0.3 + (index % 3) * 0.2)
            property real phase: index * 1.5
            property real localTime: root.time + phase
            property real progress: (localTime % 3.0) / 3.0
            property real burst: progress < 0.3 ? progress / 0.3 : 1.0
            
            Repeater {
                model: 16
                delegate: Rectangle {
                    property real angle: index * (Math.PI * 2 / 16)
                    property real dist: progress * 250
                    x: cx + Math.cos(angle) * dist
                    y: cy + Math.sin(angle) * dist + (progress * progress * 100)
                    width: Math.max(0, 6 * (1.0 - progress))
                    height: width
                    radius: width / 2
                    color: Qt.hsla((index * 0.06 + phase) % 1.0, 1.0, 0.6, Math.max(0, 1.0 - progress))
                }
            }
        }
    }
}
