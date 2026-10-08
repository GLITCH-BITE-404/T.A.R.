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

    Item {
        x: root.width / 2 - 100
        y: root.height / 2 - 100
        width: 200
        height: 200

        Repeater {
            model: 24
            delegate: Rectangle {
                property real angle: index * (Math.PI * 2 / 24) + root.time
                property real dist: 60 + Math.sin(root.time * 2 + index) * 20
                x: 100 + Math.cos(angle) * dist - width / 2
                y: 100 + Math.sin(angle) * dist - height / 2
                width: 12
                height: 12
                radius: 6
                color: Qt.hsla((index / 24 + root.time * 0.2) % 1.0, 1.0, 0.6, 0.9)
            }
        }
    }
}
