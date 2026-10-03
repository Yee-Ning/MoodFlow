const assert = require('assert')
const playlistTracks = require('../module/playlist_tracks')

describe('playlist_tracks response contract', () => {
  for (const op of ['add', 'del']) {
    it(`exposes code at the HTTP body root for ${op} without retry`, async () => {
      let calls = 0
      const result = await playlistTracks(
        { op, pid: '123', tracks: '456' },
        async () => {
          calls++
          return { status: 200, body: { code: 200, trackIds: [456] } }
        },
      )
      assert.strictEqual(result.status, 200)
      assert.strictEqual(result.body.code, 200)
      assert.strictEqual(result.body.body, undefined)
      assert.strictEqual(calls, 1)
    })
  }

  it('retains a 502 error body and never retries an unknown result', async () => {
    let calls = 0
    const result = await playlistTracks(
      { op: 'add', pid: '123', tracks: '456' },
      async () => {
        calls++
        throw { status: 502, body: { code: 502, msg: 'response lost' } }
      },
    )
    assert.strictEqual(result.body.code, 502)
    assert.strictEqual(result.body.msg, 'response lost')
    assert.strictEqual(calls, 1)
  })
})
