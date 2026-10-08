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
        onTriggered: root.time += 0.05
    }

    Repeater {
        model: 4
        delegate: Item {
            property real groupIndex: index
            property real baseX: root.width * (0.25 + (index % 2) * 0.5)
            property real baseY: root.height * (0.25 + Math.floor(index / 2) * 0.5)

            Repeater {
                model: 16
                delegate: Rectangle {
                    property real angle: index * (Math.PI * 2 / 16) + root.time * (groupIndex % 2 === 0 ? 1 : -1)
                    property real dist: 40 + Math.sin(root.time * 2 + index + groupIndex) * 15
                    x: baseX + Math.cos(angle) * dist - width / 2
                    y: baseY + Math.sin(angle) * dist - height / 2
                    width: 10
                    height: 10
                    radius: 5
                    color: Qt.hsla((groupIndex * 0.25 + index / 16 + root.time * 0.1) % 1.0, 1.0, 0.6, 0.9)
                }
            }
        }
    }
}
