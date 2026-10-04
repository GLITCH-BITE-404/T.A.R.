import QtQuick

// Matrix rain. One Text item per column (not per glyph) and one plain
// NumberAnimation each, so ~16 animated items total instead of hundreds.
Item {
    id: rain
    clip: true

    property color glyphColor: "#a6e3a1"
    property color headColor: "#cdd6f4"
    property int columns: 16
    property int glyphSize: 13
    property real strength: 1.0     // master opacity multiplier
    property bool running: true

    // JetBrains Mono has no katakana -- those glyphs fall back to Noto Sans CJK,
    // whose metrics are completely different, which shreds the column spacing.
    // Stick to glyphs the mono face actually ships so every cell is identical.
    readonly property string alphabet:
        "01234567890101ABCDEFGHJKLMNPRSTUVWXYZ#$%&*+=<>[]{}/\\|!?@01"

    function randomColumn(n) {
        var s = "";
        for (var i = 0; i < n; i++)
            s += alphabet.charAt(Math.floor(Math.random() * alphabet.length)) + "\n";
        return s;
    }

    Repeater {
        model: rain.columns

        Item {
            id: col
            width: rain.glyphSize
            height: rain.height * 2
            x: (index + 0.5) * (rain.width / rain.columns) - width / 2

            property int glyphs: Math.ceil(rain.height / rain.glyphSize) + 4
            property real speed: 9000 + (index * 977) % 11000

            Text {
                id: body
                anchors.horizontalCenter: parent.horizontalCenter
                text: rain.randomColumn(col.glyphs)
                color: rain.glyphColor
                opacity: (0.05 + (index % 4) * 0.018) * rain.strength
                font.family: "JetBrains Mono"
                font.pixelSize: rain.glyphSize
                lineHeightMode: Text.FixedHeight
                lineHeight: rain.glyphSize * 1.05
                horizontalAlignment: Text.AlignHCenter
            }

            // the bright leading glyph
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                y: body.contentHeight - rain.glyphSize * 1.2
                text: rain.alphabet.charAt((index * 7) % rain.alphabet.length)
                color: rain.headColor
                opacity: 0.22 * rain.strength
                font.family: "JetBrains Mono"
                font.pixelSize: rain.glyphSize
            }

            NumberAnimation on y {
                running: rain.running && rain.visible
                loops: Animation.Infinite
                from: -col.height
                to: rain.height
                duration: col.speed
            }

            // reshuffle the glyphs occasionally so it shimmers
            Timer {
                interval: 1400 + (index * 311) % 2200
                running: rain.running && rain.visible
                repeat: true
                onTriggered: body.text = rain.randomColumn(col.glyphs)
            }
        }
    }
}
