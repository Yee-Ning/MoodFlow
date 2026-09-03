import { useState, type ImgHTMLAttributes, type ReactNode } from 'react'
import { getNeteaseImageUrl } from '../../utils/neteaseImage'

interface NeteaseImageProps extends Omit<ImgHTMLAttributes<HTMLImageElement>, 'src'> {
  fallback: ReactNode
  size: number
  source: string
}

function NeteaseImage({
  source,
  size,
  fallback,
  onError,
  ...imageProps
}: NeteaseImageProps) {
  const [failedSource, setFailedSource] = useState('')

  if (!source || failedSource === source) {
    return fallback
  }

  return (
    <img
      {...imageProps}
      src={getNeteaseImageUrl(source, size)}
      referrerPolicy="no-referrer"
      onError={(event) => {
        setFailedSource(source)
        onError?.(event)
      }}
    />
  )
}

export default NeteaseImage
