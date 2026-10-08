// Screen shake: a screenshot of THIS screen (taken just before) shown full
// screen and shaken hard, settling over ~1.4 s -- the whole desktop looks
// like it shakes. Black shows at the edges as it moves.
import QtQuick

Item {
    id: sk
    property string shot: ""            // set by host.qml (per screen)
    Rectangle { anchors.fill: parent; color: "black" }
    Image {
        id: img
        width: sk.width; height: sk.height
        source: sk.shot ? "file://" + sk.shot : ""
        cache: false
        property real k: 1.0             // shake strength, decays to 0
        x: (Math.random() - 0.5) * 60 * k + 0 * tick.n
        y: (Math.random() - 0.5) * 40 * k + 0 * tick.n
        rotation: (Math.random() - 0.5) * 2.4 * k + 0 * tick.n
        NumberAnimation on k { from: 1; to: 0; duration: 1400; easing.type: Easing.OutQuad }
    }
    Timer { id: tick; property int n: 0; interval: 30; repeat: true; running: img.k > 0.01
            onTriggered: n++ }
}
