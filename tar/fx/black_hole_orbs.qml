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

    // Central black hole
    Rectangle {
        x: root.width / 2 - 40
        y: root.height / 2 - 40
        width: 80
        height: 80
        radius: 40
        color: "#050505"
        border.color: "#331155"
        border.width: 4
    }

    // Orbs sucked inward and spawning from sides
    Repeater {
        model: 32
        delegate: Item {
            property real spawnDelay: index * 0.4
            property real life: ((root.time + spawnDelay) % 6.0) / 6.0 // 0 to 1
            property real startSide: index % 4 // 0: left, 1: right, 2: top, 3: bottom
            
            property real startX: startSide === 0 ? -50 : (startSide === 1 ? root.width + 50 : Math.random() * root.width)
            property real startY: startSide === 2 ? -50 : (startSide === 3 ? root.height + 50 : Math.random() * root.height)
            
            property real targetX: root.width / 2
            property real targetY: root.height / 2
            
            // Move from start toward center, then disappear
            x: startX + (targetX - startX) * life - 8
            y: startY + (targetY - startY) * life - 8
            
            Rectangle {
                width: 16 * (1.0 - life * 0.7)
                height: width
                radius: width / 2
                color: Qt.hsla((index * 0.12 + root.time * 0.2) % 1.0, 1.0, 0.6, Math.max(0, 1.0 - life))
                border.color: "white"
                border.width: 1
            }
        }
    }
}
