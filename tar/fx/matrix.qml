// Matrix rain: columns of glyphs sliding down, bright head, fading tail.
// Pure scene-graph items (moved on the GPU), no canvas redraws -- smooth at
// full screen, and nothing is left behind once a column has passed.
import QtQuick

Item {
    id: rain
    readonly property int cell: 20
    readonly property int trail: 16
    readonly property string glyphs: "アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789ABCDEF:.=*+<>"
    function glyph() { return glyphs.charAt(Math.floor(Math.random() * glyphs.length)); }
    clip: true

    Repeater {
        model: Math.ceil(rain.width / rain.cell)
        Item {
            id: col
            x: index * rain.cell
            width: rain.cell
            height: rain.trail * rain.cell
            y: -height
            property real speed: 0.6 + Math.random() * 1.2      // screens per 3 s

            Column {
                Repeater {
                    model: rain.trail
                    Text {
                        // index 0 = oldest (top, faint), last = head (bright)
                        readonly property bool head: index === rain.trail - 1
                        text: rain.glyph()
                        width: rain.cell; height: rain.cell
                        horizontalAlignment: Text.AlignHCenter
                        color: head ? "#d8ffe0" : "#00ff5a"
                        opacity: head ? 1.0 : Math.pow((index + 1) / rain.trail, 1.6) * 0.85
                        font.family: "monospace"
                        font.pixelSize: rain.cell - 3
                        font.bold: true
                        // the head flickers to a new glyph now and then
                        Timer { running: parent.head; interval: 90 + Math.random() * 120; repeat: true
                                onTriggered: parent.text = rain.glyph() }
                    }
                }
            }

            NumberAnimation on y {
                id: fall
                from: -col.height - Math.random() * rain.height
                to: rain.height + 10
                duration: 3000 / col.speed * (rain.height + col.height) / Math.max(1, rain.height)
                loops: Animation.Infinite
            }
        }
    }
}
