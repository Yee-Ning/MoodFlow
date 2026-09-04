import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const server = await createServer({
  root: projectRoot,
  appType: 'custom',
  logLevel: 'silent',
  server: { middlewareMode: true },
})

try {
  const {
    AI_CHAT_TRIGGER_DRAG_THRESHOLD,
    clampAiChatTriggerPosition,
    hasExceededDragThreshold,
    parseAiChatTriggerPosition,
  } = await server.ssrLoadModule('/src/components/ai/aiChatTriggerPosition.ts')

  const bounds = {
    viewportWidth: 360,
    viewportHeight: 640,
    triggerWidth: 52,
    triggerHeight: 52,
    edgeGap: 10,
  }

  assert.deepEqual(clampAiChatTriggerPosition({ x: -80, y: -20 }, bounds), { x: 10, y: 10 })
  assert.deepEqual(clampAiChatTriggerPosition({ x: 500, y: 900 }, bounds), { x: 298, y: 578 })
  assert.deepEqual(clampAiChatTriggerPosition({ x: 120, y: 240 }, bounds), { x: 120, y: 240 })

  assert.equal(hasExceededDragThreshold(AI_CHAT_TRIGGER_DRAG_THRESHOLD - 1, 0), false)
  assert.equal(hasExceededDragThreshold(3, 4), true)

  assert.deepEqual(parseAiChatTriggerPosition('{"x":42,"y":96}'), { x: 42, y: 96 })
  assert.equal(parseAiChatTriggerPosition('{"x":"bad","y":96}'), null)
  assert.equal(parseAiChatTriggerPosition('not-json'), null)

  const triggerComponent = await readFile(
    new URL('../src/components/ai/AiChatTrigger.tsx', import.meta.url),
    'utf8',
  )
  assert.match(triggerComponent, /onPointerDown=\{handlePointerDown\}/)
  assert.match(triggerComponent, /onPointerMove=\{handlePointerMove\}/)
  assert.match(triggerComponent, /onPointerUp=\{finishPointerInteraction\}/)
  assert.match(triggerComponent, /suppressClickRef\.current/)

  process.stdout.write('AI chat trigger position checks passed.\n')
} finally {
  await server.close()
}
