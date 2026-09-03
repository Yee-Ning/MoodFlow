interface LiquidGlassFilterProps {
  animated?: boolean
  compact?: boolean
  filterId: string
  mapHref: string
  mapId: string
}

interface OffsetProps {
  animated: boolean
  input: string
  offset: number
  result: string
}

function ChromaticOffset({ animated, input, offset, result }: OffsetProps) {
  const flow = `${offset};${offset + 2};${offset - 1};${offset}`

  return (
    <feOffset in={input} dx={offset} dy="0" result={result}>
      {animated && (
        <animate
          attributeName="dx"
          values={flow}
          dur="12s"
          calcMode="spline"
          keyTimes="0;0.36;0.72;1"
          keySplines="0.42 0 0.58 1;0.42 0 0.58 1;0.42 0 0.58 1"
          repeatCount="indefinite"
        />
      )}
    </feOffset>
  )
}

function LiquidGlassFilter({
  animated = true,
  compact = false,
  filterId,
  mapHref,
  mapId,
}: LiquidGlassFilterProps) {
  const redScale = compact ? 118 : 180
  const greenScale = compact ? 108 : 170
  const blueScale = compact ? 100 : 160
  const chromaticOffset = compact ? -34 : -90

  return (
    <svg
      className="liquid-glass-filter-svg"
      width="0"
      height="0"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <filter
          id={filterId}
          colorInterpolationFilters="sRGB"
          x={compact ? '-48%' : '-12%'}
          y={compact ? '-68%' : '-28%'}
          width={compact ? '210%' : '124%'}
          height={compact ? '236%' : '156%'}
        >
          <feImage
            id={mapId}
            href={mapHref}
            x={compact ? '-24%' : '0'}
            y={compact ? '-14%' : '0'}
            width={compact ? '148%' : '100%'}
            height={compact ? '128%' : '100%'}
            preserveAspectRatio="none"
            result="map"
          />

          <feDisplacementMap
            in="SourceGraphic"
            in2="map"
            scale={redScale}
            xChannelSelector="R"
            yChannelSelector="B"
            result="dispRed"
          />
          <ChromaticOffset
            animated={animated}
            input="dispRed"
            offset={chromaticOffset}
            result="dispRedShifted"
          />
          <feMerge result="dispRedAligned">
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="dispRedShifted" />
          </feMerge>
          <feColorMatrix
            in="dispRedAligned"
            type="matrix"
            values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"
            result="red"
          />

          <feDisplacementMap
            in="SourceGraphic"
            in2="map"
            scale={greenScale}
            xChannelSelector="R"
            yChannelSelector="B"
            result="dispGreen"
          />
          <ChromaticOffset
            animated={animated}
            input="dispGreen"
            offset={chromaticOffset}
            result="dispGreenShifted"
          />
          <feMerge result="dispGreenAligned">
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="dispGreenShifted" />
          </feMerge>
          <feColorMatrix
            in="dispGreenAligned"
            type="matrix"
            values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0"
            result="green"
          />

          <feDisplacementMap
            in="SourceGraphic"
            in2="map"
            scale={blueScale}
            xChannelSelector="R"
            yChannelSelector="B"
            result="dispBlue"
          />
          <ChromaticOffset
            animated={animated}
            input="dispBlue"
            offset={chromaticOffset}
            result="dispBlueShifted"
          />
          <feMerge result="dispBlueAligned">
            <feMergeNode in="SourceGraphic" />
            <feMergeNode in="dispBlueShifted" />
          </feMerge>
          <feColorMatrix
            in="dispBlueAligned"
            type="matrix"
            values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0"
            result="blue"
          />

          <feBlend in="red" in2="green" mode="screen" result="rg" />
          <feBlend in="rg" in2="blue" mode="screen" result="output" />
          <feGaussianBlur in="output" stdDeviation="0.5" />
        </filter>
      </defs>
    </svg>
  )
}

export default LiquidGlassFilter

