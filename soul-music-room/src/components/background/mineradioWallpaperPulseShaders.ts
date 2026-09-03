/*
 * Adapted from XxHuberrr/Mineradio public/index.html preset 5,
 * "WALLPAPER PULSE". GPL-3.0-only.
 */
export const mineradioWallpaperVertexShader = /* glsl */ `
precision highp float;

uniform float uTime;
uniform float uBass;
uniform float uMid;
uniform float uTreble;
uniform float uBeat;
uniform float uEnergy;
uniform float uBurstAmt;
uniform float uPointScale;
uniform float uSpeed;
uniform float uColorBoost;
uniform float uPixel;
uniform float uColorMixT;
uniform sampler2D uCoverTex;
uniform sampler2D uPrevCoverTex;
uniform vec2 uParallax;
uniform vec3 uPrimaryColor;
uniform vec3 uSecondaryColor;
uniform vec3 uHighlightColor;

attribute vec2 aUv;
attribute float aRand;

varying vec3 vColor;
varying float vBright;
varying float vRipple;
varying float vEdgeBoost;
varying float vAlpha;
varying float vSourceLum;

#define PI 3.14159265359

vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289v(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 perm(vec4 x){return mod289v(((x*34.0)+1.0)*x);}

float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=perm(perm(perm(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=inversesqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}

float hash11(float p) {
  return fract(sin(p * 127.1) * 43758.5453123);
}

vec2 safeCoverUv(vec2 uv) {
  return clamp(uv, vec2(0.0012), vec2(0.9988));
}

void main(){
  float t = uTime * uSpeed;
  vec2 sampleUv = safeCoverUv(aUv);
  vec3 newCol = texture2D(uCoverTex, sampleUv).rgb;
  vec3 prevCol = texture2D(uPrevCoverTex, sampleUv).rgb;
  vec3 coverColor = mix(prevCol, newCol, clamp(uColorMixT, 0.0, 1.0));
  vec3 pos;

  float bassGlow = smoothstep(0.07, 0.78, uBass) * 0.34 + uBeat * 0.014;
  float midGlow = smoothstep(0.07, 0.62, uMid) * 0.42;
  float highGlow = smoothstep(0.04, 0.46, uTreble) * 0.46;
  float lane = aUv.y;
  float transition = clamp(uBurstAmt, 0.0, 1.0);
  float maxRippleAmp = 0.0;

  if (lane < 0.80) {
    float laneWarp = snoise(vec3(aUv.x * 0.42, lane * 1.7, t * 0.026)) * 0.11 + (hash11(aRand * 73.1) - 0.5) * 0.045;
    float warpedLane = clamp(lane + laneWarp, 0.0, 0.80);
    float bandCoord = warpedLane / 0.80 * 5.65 + snoise(vec3(aUv.x * 0.82, lane * 2.25, t * 0.032)) * 0.62;
    float band = floor(bandCoord);
    float local = fract(bandCoord + hash11(band * 9.13 + aRand * 2.4) * 0.18);
    float bandN = clamp((band + 0.5) / 5.65, 0.0, 1.0);
    float seed = hash11(band * 19.17 + aRand * 31.0);
    float flow = fract(aUv.x + t * (0.0034 + bandN * 0.0038 + seed * 0.0022) + seed * 0.53);
    float arc = (flow - 0.5) * PI * (1.35 + bandN * 0.72 + seed * 0.24);
    float armCurve = sin(arc + bandN * 2.2 + seed * 5.3);
    float spiralRadius = 9.2 + bandN * 11.8 + seed * 6.0 + local * 2.9;
    float x = cos(arc * 0.72 + bandN * 0.92 + seed * 1.3) * spiralRadius + (flow - 0.5) * (13.5 + bandN * 9.5);
    float ribbonPhase = flow * PI * 2.0 * (0.55 + bandN * 0.24 + seed * 0.10) + t * (0.010 + bandN * 0.007) + seed * 5.7;
    float broadWave = sin(ribbonPhase) * 0.92;
    float fineWave = sin(ribbonPhase * (1.36 + seed * 0.62) - t * 0.044 + seed * 5.0) * 0.045;
    float yBase = (bandN - 0.5) * 13.2 + armCurve * (2.3 + bandN * 1.6) + (seed - 0.5) * 1.85 + snoise(vec3(bandN * 2.0, flow * 0.62, seed)) * 0.92;
    float ridgeCenter = 0.43 + (seed - 0.5) * 0.18;
    float ridge = exp(-pow((local - ridgeCenter) / (0.25 + seed * 0.04), 2.0));
    float softMask = smoothstep(0.010, 0.12, lane) * (1.0 - smoothstep(0.72, 0.81, lane));
    float ribbonNoise = snoise(vec3(flow * 1.18 + seed, bandN * 2.0, t * 0.018)) * 0.74;
    float zLayer = mix(-23.5, 15.5, bandN) + (seed - 0.5) * 6.0;

    pos.x = x + ribbonNoise * 1.40 + sin(t * 0.012 + seed * 8.0) * 0.22;
    pos.y = yBase + broadWave + fineWave + (local - 0.5) * (0.58 + ridge * 0.14);
    pos.z = zLayer + broadWave * 1.35 + ribbonNoise * 1.85;

    float pulseLine = 0.5 + 0.5 * sin(ribbonPhase * (1.7 + seed * 0.9) - t * 0.32 + seed * 6.0);
    vec3 aurora = mix(uPrimaryColor, uSecondaryColor, bandN);
    aurora = mix(aurora, uHighlightColor, bassGlow * 0.05);
    vAlpha = (0.18 + ridge * 0.78 + pulseLine * highGlow * 0.035 + bassGlow * 0.025) * softMask * (0.96 + transition * 0.02);
    vColor = mix(coverColor, aurora, 0.62 + ridge * 0.22) * (0.76 + ridge * 0.86 + pulseLine * highGlow * 0.05 + bassGlow * 0.04);
    maxRippleAmp = max(maxRippleAmp, ridge * (0.12 + midGlow * 0.05) + pulseLine * highGlow * 0.045 + bassGlow * 0.030);
  } else {
    float q = (lane - 0.80) / 0.20;
    float seed = hash11(aRand * 917.0 + floor(q * 130.0));
    float depth = mix(-32.0, 18.0, seed);
    float drift = fract(aUv.x + t * (0.0014 + seed * 0.0048) + seed * 0.63);
    float cluster = snoise(vec3(seed * 2.0, q * 3.2, t * 0.007));
    float x = (drift - 0.5) * (45.0 + seed * 22.0) + cluster * 3.4;
    float y = (hash11(aRand * 331.0 + seed * 5.0) - 0.5) * 22.0 + sin(t * (0.018 + seed * 0.028) + seed * 7.0) * 0.86;
    float z = depth + sin(t * (0.020 + seed * 0.032) + aRand * 8.0) * 1.05;
    float twinkle = pow(0.5 + 0.5 * sin(t * (0.24 + seed * 0.42) + aRand * 17.0), 5.0);
    float dust = smoothstep(0.22, 0.98, hash11(aRand * 661.0 + floor(q * 160.0)));

    pos = vec3(x, y, z);
    vAlpha = dust * (0.16 + twinkle * 0.46 + highGlow * 0.025 + bassGlow * 0.018) * (1.0 - q * 0.06);
    vColor = mix(coverColor, vec3(0.92, 0.97, 1.0), 0.62 + twinkle * 0.14) * (0.72 + twinkle * 0.62 + bassGlow * 0.025);
    maxRippleAmp = max(maxRippleAmp, twinkle * highGlow * 0.055 + dust * bassGlow * 0.030);
  }

  if (transition > 0.001) {
    float bloom = smoothstep(0.0, 1.0, transition);
    vec2 burstVec = pos.xy + vec2(hash11(aRand * 31.0) - 0.5, hash11(aRand * 47.0) - 0.5) * 0.75;
    vec2 burstDir = burstVec / max(length(burstVec), 0.001);
    pos.xy += burstDir * bloom * 0.026;
    pos.xy += vec2(snoise(vec3(aRand, t * 0.014, 1.0)), snoise(vec3(aRand, t * 0.014, 5.0))) * bloom * 0.06;
    pos.xy *= 1.0 + bloom * 0.014;
    pos.z += (hash11(aRand * 123.0) - 0.5) * bloom * 0.18;
    vAlpha *= 0.86 + bloom * 0.22;
    maxRippleAmp = max(maxRippleAmp, bloom * 0.10);
  }

  pos.xy += uParallax;
  vSourceLum = dot(max(vColor, vec3(0.0)), vec3(0.299, 0.587, 0.114));
  vColor = pow(max(vColor, vec3(0.0)), vec3(1.0 / max(0.35, uColorBoost)));
  vBright = 0.94 + maxRippleAmp * 0.34 + uBass * 0.020 + uEnergy * 0.026 + uBurstAmt * 0.025;
  vRipple = clamp(maxRippleAmp * 1.5, 0.0, 1.0);
  vEdgeBoost = 0.0;

  vec4 mvPos = modelViewMatrix * vec4(pos, 1.0);
  float depthSize = 36.0 / max(0.5, -mvPos.z);
  float flowDrive = uBass * 0.070 + uMid * 0.046 + uTreble * 0.060 + uBurstAmt * 0.090 + uBeat * 0.055;
  float size = clamp(depthSize * (1.05 + flowDrive), 1.00, 5.45);
  gl_PointSize = size * uPixel * uPointScale;
  gl_Position = projectionMatrix * mvPos;
}
`

export const mineradioWallpaperFragmentShader = /* glsl */ `
precision highp float;

uniform sampler2D uDotTex;
uniform float uAlpha;
uniform float uParticleDim;

varying vec3 vColor;
varying float vBright;
varying float vRipple;
varying float vEdgeBoost;
varying float vAlpha;
varying float vSourceLum;

void main(){
  vec4 tex = texture2D(uDotTex, gl_PointCoord);
  if (tex.a < 0.02) discard;
  vec3 col = vColor * vBright;
  col = mix(col, col * 1.3 + vec3(0.05), vEdgeBoost * 0.35);
  col = mix(col, col * 1.2, vRipple * 0.4);
  float keepBlack = 1.0 - smoothstep(0.025, 0.115, vSourceLum);
  float nonBlack = 1.0 - keepBlack;
  float dotDist = length(gl_PointCoord - vec2(0.5)) * 2.0;
  float readableRim = smoothstep(0.44, 0.94, dotDist) * (1.0 - smoothstep(0.94, 1.08, dotDist)) * tex.a;
  float outLum = dot(col, vec3(0.299, 0.587, 0.114));
  float lightParticle = smoothstep(0.50, 0.82, outLum) * nonBlack;
  float darkParticle = (1.0 - smoothstep(0.20, 0.50, outLum)) * nonBlack;
  col = mix(col, vec3(0.0), readableRim * lightParticle * 0.38);
  col = mix(col, vec3(1.0), readableRim * darkParticle * 0.20);
  col = clamp(col, vec3(0.0), vec3(1.6));
  gl_FragColor = vec4(col, tex.a * uAlpha * uParticleDim * vAlpha);
}
`
