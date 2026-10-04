import QtQuick

// RGB-split glitch text. Three stacked Text items (red / cyan / real) whose
// offsets jitter in bursts. No shader, no layers -- cheap enough to leave
// running in a header forever.
Item {
    id: g

    property string text: ""
    property color color: "#cdd6f4"
    property string fontFamily: "JetBrains Mono"
    property int pixelSize: 26
    property bool bold: false
    property real letterSpacing: 0

    // 0 = clean, 1 = maximum tearing. Bursts ride on top of this.
    property real intensity: 0.0
    // how often a burst fires, ms. 0 disables bursts.
    property int burstInterval: 2600

    property real ox: 0     // current split offset
    property real slice: 0  // horizontal tear offset of the middle band

    implicitWidth: real.implicitWidth
    implicitHeight: real.implicitHeight

    Timer {
        interval: g.burstInterval
        running: g.burstInterval > 0 && g.visible
        repeat: true
        onTriggered: if (Math.random() > 0.35) burst.restart()
    }

    SequentialAnimation {
        id: burst
        PropertyAction { target: g; property: "slice"
                         value: (Math.random() * 10 - 5) }
        NumberAnimation { target: g; property: "ox"
                          to: 2.2 + Math.random() * 2.5; duration: 55 }
        NumberAnimation { target: g; property: "ox"; to: 0.4; duration: 70 }
        NumberAnimation { target: g; property: "ox"
                          to: 1.6 + Math.random() * 1.6; duration: 45 }
        ParallelAnimation {
            NumberAnimation { target: g; property: "ox"; to: 0; duration: 130 }
            NumberAnimation { target: g; property: "slice"; to: 0; duration: 130 }
        }
    }

    readonly property real totalOffset: ox + (intensity * 2.0)

    // red ghost
    Text {
        x: -g.totalOffset
        y: g.slice * 0.35
        text: g.text
        color: "#ff2d55"
        opacity: 0.55
        font.family: g.fontFamily
        font.pixelSize: g.pixelSize
        font.bold: g.bold
        font.letterSpacing: g.letterSpacing
        renderType: Text.NativeRendering
    }
    // cyan ghost
    Text {
        x: g.totalOffset
        y: -g.slice * 0.35
        text: g.text
        color: "#00e5ff"
        opacity: 0.55
        font.family: g.fontFamily
        font.pixelSize: g.pixelSize
        font.bold: g.bold
        font.letterSpacing: g.letterSpacing
        renderType: Text.NativeRendering
    }
    // the real one
    Text {
        id: real
        text: g.text
        color: g.color
        font.family: g.fontFamily
        font.pixelSize: g.pixelSize
        font.bold: g.bold
        font.letterSpacing: g.letterSpacing
        renderType: Text.NativeRendering
    }
}
