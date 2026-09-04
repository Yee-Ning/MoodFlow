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
    calculateAiChatPanelPlacement,
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

  assert.deepEqual(
    calculateAiChatPanelPlacement({ x: 720, y: 450 }, { width: 1440, height: 900 }),
    { left: 509, top: 70, width: 422, height: 760, originX: 211, originY: 380 },
  )
  assert.deepEqual(
    calculateAiChatPanelPlacement({ x: 26, y: 26 }, { width: 1440, height: 900 }),
    { left: 16, top: 16, width: 422, height: 760, originX: 10, originY: 10 },
  )
  assert.deepEqual(
    calculateAiChatPanelPlacement({ x: 1414, y: 874 }, { width: 1440, height: 900 }),
    { left: 1002, top: 124, width: 422, height: 760, originX: 412, originY: 750 },
  )
  assert.deepEqual(
    calculateAiChatPanelPlacement({ x: 330, y: 90 }, { width: 390, height: 844 }),
    { left: 0, top: 0, width: 390, height: 844, originX: 330, originY: 90 },
  )

  const triggerComponent = await readFile(
    new URL('../src/components/ai/AiChatTrigger.tsx', import.meta.url),
    'utf8',
  )
  assert.match(triggerComponent, /onPointerDown=\{handlePointerDown\}/)
  assert.match(triggerComponent, /onPointerMove=\{handlePointerMove\}/)
  assert.match(triggerComponent, /onPointerUp=\{finishPointerInteraction\}/)
  assert.match(triggerComponent, /suppressClickRef\.current/)
  assert.match(triggerComponent, /onOpen\(\{[\s\S]*rect\.left \+ rect\.width \/ 2/)
  assert.doesNotMatch(triggerComponent, /isOpen|<X/)

  const panelComponent = await readFile(
    new URL('../src/components/ai/AiChatPanel.tsx', import.meta.url),
    'utf8',
  )
  assert.match(panelComponent, /\{!isOpen && <AiChatTrigger onOpen=\{handleOpen\} \/>\}/)
  assert.match(panelComponent, /onClose=\{close\}/)
  assert.doesNotMatch(panelComponent, /onClick=\{close\}|KeyboardEvent|Escape/)

  const { useAiChatStore } = await server.ssrLoadModule('/src/store/aiChatStore.ts')
  useAiChatStore.getState().close()
  useAiChatStore.getState().open()
  assert.equal(useAiChatStore.getState().isOpen, true)
  useAiChatStore.getState().open()
  assert.equal(useAiChatStore.getState().isOpen, true)
  useAiChatStore.getState().close()
  assert.equal(useAiChatStore.getState().isOpen, false)

  process.stdout.write('AI chat trigger position checks passed.\n')
} finally {
  await server.close()
}
