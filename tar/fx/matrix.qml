// Matrix rain: columns of glyphs sliding down, bright head, fading tail.
//
// Cheap on purpose: each column is ONE pre-coloured text block (tail colours
// baked in, rebuilt only when the column wraps) plus one flickering head
// glyph -- ~2 items per column instead of one per glyph (that version ran a
// whole CPU core). The fall starts only once the screen has a real size: the
// overlay is created at height 0, which used to pin every column to the top.
import QtQuick

Item {
    id: rain
    readonly property int cell: 22
    readonly property int trail: 14
    readonly property string glyphs: "アイウエオカキクケコサシスセソタチツテトナニヌネノ0123456789ABCDEF:.=*+<>"
    readonly property bool sized: width > 0 && height > 0
    function glyph() { return glyphs.charAt(Math.floor(Math.random() * glyphs.length)); }
    // tail: oldest (top) faint -> newest bright; colours as #AARRGGBB
    function tailText() {
        var out = [];
        for (var i = 0; i < trail; i++) {
            var a = Math.round(Math.pow((i + 1) / trail, 1.6) * 0.85 * 255);
            var hex = ("0" + a.toString(16)).slice(-2);
            out.push("<font color='#" + hex + "00ff5a'>" + glyph() + "</font>");
        }
        return out.join("<br>");
    }
    clip: true

    // ONE ticker for everything at 25 fps -- Qt animations repaint at 60 fps
    // and a full-screen overlay at 60 fps was most of the CPU cost
    Timer {
        interval: 40; repeat: true; running: rain.sized
        onTriggered: {
            for (var i = 0; i < cols.count; i++) {
                var c = cols.itemAt(i);
                if (!c) continue;
                if (c.wait > 0) { c.wait--; continue; }
                c.y += c.vy;
                if (c.y > rain.height + 10) c.restart();
                if (Math.random() < 0.12) c.flicker();
            }
        }
    }

    Repeater {
        id: cols
        model: rain.sized ? Math.ceil(rain.width / rain.cell) : 0
        Item {
            id: col
            x: index * rain.cell
            width: rain.cell
            height: (rain.trail + 1) * rain.cell
            y: -height

            Text {
                id: tail
                width: rain.cell
                textFormat: Text.StyledText
                text: rain.tailText()
                horizontalAlignment: Text.AlignHCenter
                lineHeightMode: Text.FixedHeight
                lineHeight: rain.cell
                font.family: "monospace"
                font.pixelSize: rain.cell - 4
                font.bold: true
            }
            Text {
                id: head
                y: rain.trail * rain.cell
                width: rain.cell
                text: rain.glyph()
                horizontalAlignment: Text.AlignHCenter
                color: "#d8ffe0"
                font.family: "monospace"
                font.pixelSize: rain.cell - 4
                font.bold: true
            }
            function flicker() { head.text = rain.glyph(); }
            property real vy: 0                  // px per tick, set on (re)start
            property int wait: Math.floor(Math.random() * 60)   // ticks before it starts
            function restart() {
                y = -height;
                vy = (0.5 + Math.random() * 1.1) * rain.height / 75;   // ~3 s per screen at 25 fps
                tail.text = rain.tailText();
                wait = Math.floor(Math.random() * 40);
            }
            Component.onCompleted: restart()
        }
    }
}
