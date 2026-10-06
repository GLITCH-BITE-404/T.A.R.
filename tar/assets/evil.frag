#version 300 es
// T.A.R. EVIL MODE screen grade: keeps brightness and contrast, pushes colour
// toward blood red, darkens the edges. Applied with
//   hyprctl keyword decoration:screen_shader <this file>
// and removed with screen_shader [[EMPTY]] -- nothing is written to config.
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;

void main() {
    vec4 c = texture(tex, v_texcoord);
    float l = dot(c.rgb, vec3(0.299, 0.587, 0.114));
    vec3 red = vec3(l * 1.25, l * 0.32, l * 0.36);       // monochrome red version
    vec3 graded = mix(c.rgb, red, 0.42);                 // 42% of the way there
    vec2 d = v_texcoord - 0.5;
    float vig = 1.0 - smoothstep(0.35, 0.85, length(d)) * 0.35;
    fragColor = vec4(graded * vig, c.a);
}
