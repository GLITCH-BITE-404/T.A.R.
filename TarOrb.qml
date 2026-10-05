import QtQuick
import QtQuick.Effects


// The T.A.R. core. Purely visual -- it reacts to `mode` and `level` and knows
// nothing about the model. Original orb choreography kept; per-particle blur
// layers dropped (20 render targets) so this holds 60fps next to a live chat.
Item {
    id: orbRoot

    // "boot" | "idle" | "listening" | "thinking" | "speaking" | "error"
    // Master animation gate. QML keeps NumberAnimations ticking on a hidden
    // item -- 62 of them here, several infinite -- which burned CPU whenever
    // the panel was in the other view mode or behind the setup pane.
    property bool live: visible
    property string mode: "boot"
    // 0..1 activity, drives the speaking/listening amplitude
    property real level: 0.0

    property int coreSize: 150

    MatugenColors { id: theme }

    readonly property color cText: theme.text
    readonly property color cSurface0: theme.surface0
    readonly property color cSurface2: theme.surface2

    // Mode drives hue: mauve/pink idle, blue/sapphire thinking, teal listening, red error.
    property bool evil: false               // EVIL MODE: the core burns red
    readonly property color hotA: evil ? (mode === "thinking" ? "#ff7a3d" : "#ff2a3d")
                               : mode === "error" ? theme.red
                               : mode === "listening" ? theme.teal
                               : mode === "thinking" ? theme.blue
                               : theme.mauve
    readonly property color hotB: evil ? "#8b0a1a"
                               : mode === "error" ? theme.maroon
                               : mode === "listening" ? theme.green
                               : mode === "thinking" ? theme.sapphire
                               : theme.pink

    // Animation tempo. Thinking spins up, idle settles down.
    readonly property real tempo: mode === "thinking" ? 3.2
                                : mode === "speaking" ? 2.0
                                : mode === "listening" ? 1.6
                                : 1.0

    // calmState 1.0 == fully settled idle. Activity pulls it back down.
    property real calmState: 0.0
    property real activity: (mode === "idle" || mode === "boot")
                                     ? 0.0 : 1.0
    Behavior on activity { NumberAnimation { duration: 500; easing.type: Easing.InOutSine } }

    property real popShockwave: 0.0
    property bool booted: false

    // Smoothed level so a jumpy input doesn't judder the orb.
    property real smoothLevel: 0.0
    Behavior on smoothLevel { NumberAnimation { duration: 90; easing.type: Easing.OutSine } }
    onLevelChanged: smoothLevel = Math.max(0, Math.min(1, level))

    // --- continuous time engine (single driver for every breath) ---
    property real time: 0
    NumberAnimation on time {
        from: 0; to: Math.PI * 2; duration: 15000
        loops: Animation.Infinite; running: orbRoot.live
    }
    readonly property real breathA: (Math.sin(time * 3 * tempo) + 1) / 2
    readonly property real breathB: (Math.sin(time * 3 * tempo + 0.6) + 1) / 2
    readonly property real breathC: (Math.sin(time * 3 * tempo + 1.2) + 1) / 2

    // --- slow palette drift ---
    property real baseBlend: 0.0
    SequentialAnimation on baseBlend {
        loops: Animation.Infinite; running: orbRoot.live
        NumberAnimation { to: 1.0; duration: 15000; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.0; duration: 15000; easing.type: Easing.InOutSine }
    }
    readonly property color basePurple: Qt.tint(
        hotA, Qt.rgba(hotB.r, hotB.g, hotB.b, baseBlend))

    property real accentBlend: 0.0
    SequentialAnimation on accentBlend {
        loops: Animation.Infinite; running: orbRoot.live
        NumberAnimation { to: 1.0; duration: 15000; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.0; duration: 15000; easing.type: Easing.InOutSine }
    }
    readonly property color accentLavender: evil
        ? Qt.tint("#c4102a", Qt.rgba(1, 0.48, 0.24, accentBlend))
        : Qt.tint(theme.blue, Qt.rgba(theme.sapphire.r, theme.sapphire.g, theme.sapphire.b, accentBlend))

    // Fire the transformation pop once the backend is actually alive.
    function boot() {
        if (booted) return;
        booted = true;
        introSequence.start();
    }
    function pulse() { shockwaveAnim.restart(); }

    // ---------------------------------------------------------------- glow aura
    Item {
        id: orbGlow
        anchors.centerIn: parent
        width: orbRoot.coreSize
        height: width

        property real baseOpacity: 0.0
        property real baseScale: 0.8

        opacity: baseOpacity * (1.0 - (orbRoot.calmState * 0.2))
                 * (0.8 + (orbRoot.breathA * 0.2))
        scale: baseScale + (orbRoot.breathA * 0.03)
               + (orbRoot.smoothLevel * 0.12)

        Repeater {
            model: 2
            Rectangle {
                anchors.centerIn: parent
                width: parent.width + (index * 40) + 20
                height: width
                radius: width / 2
                color: orbRoot.basePurple
                opacity: index === 0 ? 0.12 : 0.05
                antialiasing: true
            }
        }
    }

    // ------------------------------------------------------- diffuse shockwave
    Rectangle {
        anchors.centerIn: parent
        width: orbRoot.coreSize
        height: width
        radius: width / 2
        color: orbRoot.accentLavender
        opacity: orbRoot.popShockwave * 0.12
        scale: 1.0 + (orbRoot.popShockwave * 0.8)
        antialiasing: true
        visible: orbRoot.popShockwave > 0.001
    }

    SequentialAnimation {
        id: shockwaveAnim
        NumberAnimation { target: orbRoot; property: "popShockwave"
                          from: 0.0; to: 1.0; duration: 150; easing.type: Easing.OutCubic }
        NumberAnimation { target: orbRoot; property: "popShockwave"
                          to: 0.0; duration: 1200; easing.type: Easing.OutQuart }
    }

    // ------------------------------------------------------------ center wrapper
    Item {
        id: worldCenter
        width: orbRoot.coreSize
        height: width

        property real driftX: 0
        property real driftY: 0

        SequentialAnimation on driftX {
            loops: Animation.Infinite; running: orbRoot.live
            NumberAnimation { to: 2; duration: 7450; easing.type: Easing.InOutSine }
            NumberAnimation { to: -1.5; duration: 6920; easing.type: Easing.InOutSine }
        }
        SequentialAnimation on driftY {
            loops: Animation.Infinite; running: orbRoot.live
            NumberAnimation { to: 1.5; duration: 8210; easing.type: Easing.InOutSine }
            NumberAnimation { to: -2; duration: 7630; easing.type: Easing.InOutSine }
        }

        anchors.centerIn: parent
        anchors.horizontalCenterOffset: driftX
        anchors.verticalCenterOffset: driftY
        rotation: orbRoot.calmState * Math.sin(orbRoot.time * 2) * 2.0

        Item {
            id: orb
            anchors.fill: parent
            scale: 1.0 + (orbRoot.smoothLevel * 0.06)

            // 1. pre-boot loading shell
            Rectangle {
                id: loadingShell
                anchors.fill: parent
                radius: width / 2
                antialiasing: true
                opacity: 1.0
                visible: opacity > 0.01
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: orbRoot.cSurface2 }
                    GradientStop { position: 1.0; color: orbRoot.cSurface0 }
                }
            }

            // 2. activated energy core
            Item {
                id: activeEnergyCore
                anchors.fill: parent
                opacity: 0.0
                visible: opacity > 0.01
                scale: 1.0 + (orbRoot.breathB * 0.015)

                // oscillating base gradient
                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    antialiasing: true

                    property real oscRotation: 0
                    SequentialAnimation on oscRotation {
                        loops: Animation.Infinite; running: orbRoot.live
                        NumberAnimation { to: 15; duration: 6000; easing.type: Easing.InOutSine }
                        NumberAnimation { to: -15; duration: 6000; easing.type: Easing.InOutSine }
                    }
                    rotation: oscRotation

                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop {
                            position: 0.0
                            color: orbRoot.basePurple
                            SequentialAnimation on position {
                                loops: Animation.Infinite; running: orbRoot.live
                                NumberAnimation { to: 0.2; duration: 5000; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 0.0; duration: 5000; easing.type: Easing.InOutSine }
                            }
                        }
                        GradientStop {
                            position: 1.0
                            color: orbRoot.accentLavender
                            SequentialAnimation on position {
                                loops: Animation.Infinite; running: orbRoot.live
                                NumberAnimation { to: 0.8; duration: 4500; easing.type: Easing.InOutSine }
                                NumberAnimation { to: 1.0; duration: 4500; easing.type: Easing.InOutSine }
                            }
                        }
                    }
                }

                // circulating inner core -- the one blur worth paying for
                Item {
                    anchors.fill: parent
                    opacity: 0.3 + (orbRoot.breathB * 0.2)
                    Rectangle {
                        anchors.centerIn: parent
                        width: parent.width * (0.6 + orbRoot.smoothLevel * 0.25)
                        height: width
                        radius: width / 2
                        color: orbRoot.accentLavender
                        opacity: 0.4
                        layer.enabled: true
                        layer.effect: MultiEffect { blurEnabled: true; blurMax: 32; blur: 1.0 }
                    }
                }

                // counter-oscillating band
                Rectangle {
                    anchors.fill: parent
                    radius: width / 2
                    antialiasing: true
                    opacity: 0.8

                    property real maskRotation: 0
                    SequentialAnimation on maskRotation {
                        loops: Animation.Infinite; running: orbRoot.live
                        NumberAnimation { to: -20; duration: 7000; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 20; duration: 7000; easing.type: Easing.InOutSine }
                    }
                    rotation: maskRotation

                    gradient: Gradient {
                        orientation: Gradient.Vertical
                        GradientStop { position: 0.0; color: "transparent" }
                        GradientStop { position: 0.4; color: orbRoot.accentLavender }
                        GradientStop { position: 0.6; color: orbRoot.accentLavender }
                        GradientStop { position: 1.0; color: "transparent" }
                    }
                }

                // light refraction sweep
                Rectangle {
                    id: refractionLayer
                    anchors.fill: parent
                    radius: width / 2
                    antialiasing: true
                    rotation: 25
                    color: "transparent"
                    opacity: 1.0 - (orbRoot.calmState * 0.2)

                    property real sweepPos: 0.0
                    SequentialAnimation on sweepPos {
                        loops: Animation.Infinite; running: orbRoot.live
                        NumberAnimation { from: -0.5; to: 1.5; duration: 8000; easing.type: Easing.InOutSine }
                        PauseAnimation { duration: 4000 }
                    }

                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: Math.max(0.0, Math.min(1.0, refractionLayer.sweepPos - 0.2)); color: "transparent" }
                        GradientStop { position: Math.max(0.0, Math.min(1.0, refractionLayer.sweepPos)); color: Qt.alpha(orbRoot.cText, 0.08) }
                        GradientStop { position: Math.max(0.0, Math.min(1.0, refractionLayer.sweepPos + 0.2)); color: "transparent" }
                    }
                }

                // inner rim light
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 1
                    radius: width / 2
                    color: "transparent"
                    border.width: 1.5
                    border.color: Qt.rgba(orbRoot.cText.r, orbRoot.cText.g, orbRoot.cText.b,
                                          0.15 + orbRoot.breathA * 0.1)
                    antialiasing: true
                }
            }
        }
    }

    // ------------------------------------------------- thinking ring (new state)
    Item {
        anchors.centerIn: parent
        width: orbRoot.coreSize + 34
        height: width
        opacity: orbRoot.mode === "thinking" ? 1.0 : 0.0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 320 } }

        rotation: orbRoot.time * 90

        Repeater {
            model: 3
            Rectangle {
                property real a: (index * 2 * Math.PI / 3)
                width: 5; height: 5; radius: 2.5
                antialiasing: true
                color: orbRoot.accentLavender
                opacity: 0.8 - index * 0.2
                x: parent.width / 2 + Math.cos(a) * (parent.width / 2) - width / 2
                y: parent.height / 2 + Math.sin(a) * (parent.height / 2) - height / 2
            }
        }
    }

    // ------------------------------------------------- master cinematic sequence
    SequentialAnimation {
        id: introSequence
        running: false

        PauseAnimation { duration: 120 }

        // Phase 1: wind-up
        NumberAnimation {
            target: loadingShell; property: "rotation"
            from: 0; to: 360; duration: 1000; easing.type: Easing.InCubic
        }
        // Phase 2: anticipation
        NumberAnimation { target: orb; property: "scale"; to: 0.96
                          duration: 250; easing.type: Easing.InOutSine }
        PauseAnimation { duration: 100 }

        // Phase 3: the transformation pop
        ParallelAnimation {
            NumberAnimation { target: loadingShell; property: "opacity"; to: 0.0; duration: 150 }
            NumberAnimation { target: activeEnergyCore; property: "opacity"; to: 1.0; duration: 300 }
            SequentialAnimation {
                NumberAnimation { target: orb; property: "scale"; to: 1.05
                                  duration: 200; easing.type: Easing.OutCubic }
                NumberAnimation { target: orb; property: "scale"; to: 1.0
                                  duration: 800; easing.type: Easing.InOutSine }
            }
            SequentialAnimation {
                NumberAnimation { target: orbRoot; property: "popShockwave"
                                  from: 0.0; to: 1.0; duration: 150; easing.type: Easing.OutCubic }
                NumberAnimation { target: orbRoot; property: "popShockwave"
                                  to: 0.0; duration: 1200; easing.type: Easing.OutQuart }
            }
            NumberAnimation { target: orbGlow; property: "baseOpacity"; to: 1.0
                              duration: 400; easing.type: Easing.InOutSine }
            NumberAnimation { target: orbGlow; property: "baseScale"; to: 1.0
                              duration: 600; easing.type: Easing.OutBack }
        }

        // Phase 4: settle
        NumberAnimation {
            target: orbRoot; property: "calmState"
            from: 0.0; to: 1.0; duration: 2000; easing.type: Easing.InOutSine
        }
    }
}
