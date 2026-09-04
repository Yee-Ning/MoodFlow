import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const projectRoot = new URL('..', import.meta.url)
const css = await readFile(new URL('src/components/ai/ai-chat.css', projectRoot), 'utf8')
const messageList = await readFile(new URL('src/components/ai/AiMessageList.tsx', projectRoot), 'utf8')

assert.match(
  css,
  /\.liquid-glass-surface\.ai-chat-panel\.is-mineradio-liquid\s*>\s*\.liquid-glass-surface__content\s*\{[^}]*display:\s*grid;[^}]*grid-template-rows:\s*auto\s+minmax\(0,\s*1fr\)\s+auto;/s,
  'chat content must reserve fixed rows for the header and composer around a constrained message row',
)
assert.match(
  css,
  /\.ai-message-list\s*\{[^}]*min-height:\s*0;[^}]*overflow-x:\s*hidden;[^}]*overflow-y:\s*auto;/s,
  'message history must scroll vertically inside the constrained grid row',
)
assert.match(
  messageList,
  /ref=\{listRef\}[\s\S]*onScroll=\{handleScroll\}/,
  'message history must track its own scroll position',
)
assert.doesNotMatch(
  messageList,
  /scrollIntoView/,
  'message updates must not scroll the whole page',
)

process.stdout.write(`AI chat layout checks passed for ${fileURLToPath(projectRoot)}.\n`)
