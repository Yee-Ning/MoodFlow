const NETEASE_IMAGE_HOST = /(^|\.)music\.126\.net$/i

export function getNeteaseImageUrl(source: string, size: number) {
  if (!source) return ''

  try {
    const remoteUrl = new URL(source)
    if (!NETEASE_IMAGE_HOST.test(remoteUrl.hostname)) {
      return source
    }

    const proxyUrl = new URL('https://images.weserv.nl/')
    proxyUrl.searchParams.set('url', source.replace(/^https?:\/\//i, ''))
    proxyUrl.searchParams.set('w', String(size))
    proxyUrl.searchParams.set('h', String(size))
    proxyUrl.searchParams.set('fit', 'cover')
    return proxyUrl.toString()
  } catch {
    return source
  }
}
