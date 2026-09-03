export const particleVertexShader = /* glsl */ `
  uniform float uTime;
  uniform float uPlayback;
  uniform float uFlowStrength;
  uniform float uParallaxStrength;
  uniform float uRepulsionStrength;
  uniform float uRepulsionRadius;
  uniform float uPointScale;
  uniform float uPixelRatio;
  uniform vec2 uMouse;
  uniform vec2 uResolution;

  attribute float aDepth;
  attribute float aOpacity;
  attribute float aPhase;
  attribute float aSize;
  attribute float aSpeed;
  attribute vec3 aTint;

  varying float vOpacity;
  varying vec3 vTint;

  mat2 rotate2d(float angle) {
    float sine = sin(angle);
    float cosine = cos(angle);
    return mat2(cosine, -sine, sine, cosine);
  }

  void main() {
    float motionRate = mix(1.0, 1.12, uPlayback);
    float localTime = uTime * aSpeed * motionRate;
    float depthMotion = mix(0.54, 1.16, aDepth);
    vec2 base = position.xy;

    float rotationDirection = mix(-1.0, 1.0, step(0.0, sin(aPhase)));
    float rotation = rotationDirection * localTime * 0.042
      + sin(localTime * 0.34 + aPhase) * 0.006;
    vec2 moved = rotate2d(rotation) * base;
    vec2 flow = vec2(
      sin(localTime + aPhase + base.y * 4.2),
      cos(localTime * 0.79 + aPhase * 1.37 + base.x * 3.6)
    );
    flow += 0.42 * vec2(
      sin(localTime * 0.43 + base.x * 7.0 - base.y * 2.1),
      cos(localTime * 0.37 + base.y * 6.4 + base.x * 1.8)
    );
    moved += flow * uFlowStrength * depthMotion;
    moved += uMouse * uParallaxStrength * depthMotion;

    if (uRepulsionStrength > 0.0) {
      float aspect = max(1.0, uResolution.x / max(1.0, uResolution.y));
      vec2 delta = moved - uMouse;
      vec2 aspectDelta = vec2(delta.x * aspect, delta.y);
      float distanceToMouse = length(aspectDelta);
      float influence = 1.0 - smoothstep(0.0, uRepulsionRadius, distanceToMouse);
      vec2 direction = normalize(aspectDelta + vec2(0.0001));
      direction.x /= aspect;
      moved += direction * influence * uRepulsionStrength * depthMotion;
    }

    gl_Position = vec4(moved, 0.0, 1.0);
    gl_PointSize = aSize * uPointScale * uPixelRatio * mix(0.98, 1.02, uPlayback);

    float centerDistance = length(base * vec2(0.82, 1.0));
    float centerProtection = mix(0.72, 1.0, smoothstep(0.2, 0.82, centerDistance));
    float twinkle = 0.78 + 0.22 * sin(uTime * (0.42 + aSpeed) + aPhase);
    vOpacity = aOpacity * centerProtection * twinkle * mix(0.96, 1.1, uPlayback);
    vTint = aTint;
  }
`

export const particleFragmentShader = /* glsl */ `
  uniform float uGlowStrength;

  varying float vOpacity;
  varying vec3 vTint;

  void main() {
    vec2 centered = gl_PointCoord - vec2(0.5);
    float distanceFromCenter = length(centered) * 2.0;
    if (distanceFromCenter > 1.0) discard;

    float core = 1.0 - smoothstep(0.08, 0.5, distanceFromCenter);
    float halo = 1.0 - smoothstep(0.18, 1.0, distanceFromCenter);
    float alpha = vOpacity * (core + halo * 0.5 * uGlowStrength);
    vec3 luminousTint = mix(vTint, vec3(0.9, 0.97, 1.0), 0.18);
    gl_FragColor = vec4(luminousTint * 1.16, min(alpha, 0.82));
  }
`
