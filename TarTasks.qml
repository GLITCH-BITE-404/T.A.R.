import QtQuick
import Quickshell
import Quickshell.Io
import "."

// Background tasks. A card for the running task (what, status, progress,
// PAUSE/KILL) and a readable feed of what it thinks and does, round by round:
//   ◆ thinking   ▸ doing   ✓ worked   ✗ failed   ── round N ──
// Reads loop.json once a second (also while folded, so the host knows a task
// exists and can show the TASKS button).
TarDeck {
    id: tasks

    property var task: null
    property real now: Date.now() / 1000
    readonly property bool active: task !== null
    readonly property string dataDir: Quickshell.env("TAR_DATA")
        || (Quickshell.env("HOME") + "/.local/share/bite-os/tar")
    readonly property string tools: Quickshell.env("HOME")
        + "/.config/hypr/scripts/quickshell/tar/tar_tools.py"

    // ---------------------------------------------------------------- data
    Process {
        id: reader
        command: ["cat", tasks.dataDir + "/loop.json"]
        stdout: StdioCollector {
            onStreamFinished: {
                var t = (this.text || "").trim();
                if (t === "") { tasks.task = null; return; }
                try { tasks.task = JSON.parse(t); } catch (e) { /* mid-write */ }
            }
        }
        onExited: (code) => { if (code !== 0) tasks.task = null; }
    }
    Timer {
        interval: 1000; repeat: true; running: true; triggeredOnStart: true
        onTriggered: { tasks.now = Date.now() / 1000; if (!reader.running) reader.running = true; }
    }
    Process { id: ctl; property var argv: []; command: argv }
    function control(action) {
        ctl.argv = ["python3", tasks.tools, "run", action];
        ctl.running = true;
        reader.running = true;
    }
    function fmt(sec) {
        sec = Math.max(0, Math.floor(sec));
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), ss = sec % 60;
        return (h > 0 ? h + "h " : "") + (h > 0 || m > 0 ? m + "m " : "") + ss + "s";
    }
    readonly property bool paused: active && !!task.paused
    readonly property string taskState: !active ? "" :
        (paused ? "paused"
         : (now - (task.beat || now) > 120 ? "not responding"
            : ({"working": "working", "waiting": "waiting for next round",
                "autoclicking": "autoclicking", "starting": "starting",
                "failed": "failed"})[task.status] || (task.status || "running")))
    readonly property real elapsed: active
        ? (paused ? (task.paused_at || now) : now) - (task.started || now) : 0
    readonly property real total: active && task.until ? (task.until - task.started) : 0
    readonly property color stateColor: paused ? theme.yellow
        : (taskState === "not responding" || taskState === "failed") ? theme.red : theme.green

    glyph: "\u{f0954}"
    title: "TASKS"
    status: active ? taskState + (task.round ? "  ·  round " + task.round : "") : "nothing running"
    statusColor: active ? stateColor : theme.overlay0
    statusPulse: active && !paused

    Column {
        id: top
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: tasks.s(10)

        // ---- the task card
        Rectangle {
            width: parent.width
            height: card.implicitHeight + tasks.s(24)
            radius: tasks.s(5)
            color: Qt.rgba(tasks.theme.mantle.r, tasks.theme.mantle.g, tasks.theme.mantle.b, 0.7)
            border.width: 1
            border.color: Qt.rgba(tasks.stateColor.r, tasks.stateColor.g, tasks.stateColor.b, 0.45)

            Column {
                id: card
                anchors.left: parent.left; anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: tasks.s(12)
                spacing: tasks.s(10)

                Text {
                    width: parent.width
                    text: tasks.active ? tasks.task.task : ""
                    color: tasks.theme.text
                    font.family: "JetBrains Mono"
                    font.pixelSize: tasks.s(11)
                    wrapMode: Text.Wrap
                    maximumLineCount: 4
                    elide: Text.ElideRight
                }
                Text {
                    visible: tasks.active && !!tasks.task.autoclick
                    text: tasks.active ? "autoclicking \"" + tasks.task.autoclick + "\"  ·  "
                                         + (tasks.task.clicks || 0) + " clicks" : ""
                    color: tasks.theme.subtext0
                    font.family: "JetBrains Mono"; font.pixelSize: tasks.s(9)
                }

                // progress
                Column {
                    width: parent.width
                    spacing: tasks.s(4)
                    Rectangle {
                        width: parent.width; height: tasks.s(6); radius: height / 2
                        color: Qt.rgba(tasks.theme.surface1.r, tasks.theme.surface1.g, tasks.theme.surface1.b, 0.6)
                        Rectangle {
                            height: parent.height; radius: height / 2
                            width: tasks.total > 0
                                ? parent.width * Math.min(1, Math.max(0, tasks.elapsed / tasks.total)) : 0
                            color: tasks.stateColor
                            Behavior on width { NumberAnimation { duration: 400 } }
                        }
                    }
                    Item {
                        width: parent.width; height: tasks.s(14)
                        Text {
                            text: tasks.fmt(tasks.elapsed) + (tasks.total > 0 ? "  /  " + tasks.fmt(tasks.total) : "")
                            color: tasks.theme.subtext0
                            font.family: "JetBrains Mono"; font.pixelSize: tasks.s(9)
                        }
                        Text {
                            anchors.right: parent.right
                            visible: tasks.active && tasks.task.status === "waiting" && !tasks.paused
                            text: tasks.active ? "next round in "
                                  + tasks.fmt((tasks.task.next_at || tasks.now) - tasks.now) : ""
                            color: tasks.theme.overlay1
                            font.family: "JetBrains Mono"; font.pixelSize: tasks.s(9)
                        }
                    }
                }

                Row {
                    spacing: tasks.s(8)
                    TarHudButton {
                        theme: tasks.theme; accent: tasks.theme.yellow; scaleFn: tasks.scaleFn
                        glyph: tasks.paused ? "\u{f040a}" : "\u{f03e4}"
                        label: tasks.paused ? "RESUME" : "PAUSE"
                        active: tasks.paused
                        onClicked: tasks.control(tasks.paused ? "resume_task" : "pause_task")
                    }
                    TarHudButton {
                        theme: tasks.theme; accent: tasks.theme.red; scaleFn: tasks.scaleFn
                        glyph: "\u{f0156}"; label: "KILL"
                        onClicked: tasks.control("stop_task")
                    }
                }
            }
        }

        Text {
            text: "WHAT IT'S THINKING"
            color: tasks.theme.overlay1
            font.family: "JetBrains Mono"
            font.pixelSize: tasks.s(9)
            font.bold: true
            font.letterSpacing: tasks.s(1.5)
        }
    }

    // ---- reasoning feed (newest at the bottom)
    ListView {
        id: feed
        anchors.top: top.bottom
        anchors.topMargin: tasks.s(8)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        clip: true
        spacing: tasks.s(7)
        model: tasks.active ? (tasks.task.feed || []) : []
        onCountChanged: Qt.callLater(feed.positionViewAtEnd)
        delegate: Item {
            required property var modelData
            readonly property string k: modelData.k
            width: feed.width
            height: k === "round" ? tasks.s(18) : Math.max(tasks.s(16), msg.implicitHeight)

            // round divider
            Row {
                visible: k === "round"
                anchors.centerIn: parent
                spacing: tasks.s(6)
                Rectangle { width: tasks.s(30); height: 1; color: tasks.theme.surface2; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: modelData.t
                    color: tasks.theme.overlay0
                    font.family: "JetBrains Mono"; font.pixelSize: tasks.s(8)
                }
                Rectangle { width: tasks.s(30); height: 1; color: tasks.theme.surface2; anchors.verticalCenter: parent.verticalCenter }
            }
            Text {
                id: icon
                visible: k !== "round"
                width: tasks.s(14)
                text: k === "think" ? "◆" : k === "doing" ? "▸" : k === "warn" ? "✗" : "✓"
                color: k === "think" ? tasks.accent : k === "doing" ? tasks.theme.overlay1
                     : k === "warn" ? tasks.theme.red : tasks.theme.green
                font.family: "JetBrains Mono"
                font.pixelSize: tasks.s(10)
            }
            Text {
                id: msg
                visible: k !== "round"
                anchors.left: icon.right
                anchors.right: parent.right
                text: modelData.t.replace(/ ✓$| ✗$/, "").replace(/^→ /, "")
                color: k === "think" ? tasks.theme.text
                     : k === "doing" ? tasks.theme.overlay1 : tasks.theme.subtext1
                font.family: "JetBrains Mono"
                font.pixelSize: tasks.s(10)
                font.italic: k === "think"
                wrapMode: Text.Wrap
            }
        }
        Text {
            visible: feed.count === 0
            text: "waiting for the first round…"
            color: tasks.theme.overlay0
            font.family: "JetBrains Mono"; font.pixelSize: tasks.s(10)
        }
    }
}
