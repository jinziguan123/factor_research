<script setup lang="ts">
/**
 * Python 源码编辑器（CodeMirror 6 + one-dark 主题）
 *
 * 专门给"编辑/新建因子"用：
 * - Python 高亮 + 括号匹配 + 自动缩进 + Tab=4 空格
 * - Tab / Shift+Tab 基于选区行首整块缩进（Python 约定），不是把 Tab 字符写进代码
 * - 行号 + 激活行高亮；字号默认 13px，monospace
 *
 * 用 ``vue-codemirror`` 封装：它实现了 v-model + CodeMirror extensions props，
 * 省掉手写 EditorState / EditorView 生命周期管理。
 */
import { computed } from 'vue'
import { Codemirror } from 'vue-codemirror'
import { python } from '@codemirror/lang-python'
import { oneDark } from '@codemirror/theme-one-dark'
import { indentUnit } from '@codemirror/language'
import { keymap, EditorView } from '@codemirror/view'
import { indentWithTab } from '@codemirror/commands'

const props = defineProps<{
  modelValue: string
  disabled?: boolean
  /** 可视高度（默认 480px 给个够用的编辑空间） */
  height?: string
  /** 只读模式（用于"查看源码"；编辑场景走 disabled=false） */
  readonly?: boolean
  /** 覆盖默认的 placeholder 文本（只在 modelValue 空时显示） */
  placeholder?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
}>()

const value = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// 编辑器扩展：Python 模式 + Tab=4 空格 + Tab/Shift+Tab 缩进 + 不自动换行（代码阅读习惯）
const extensions = [
  python(),
  oneDark,
  indentUnit.of('    '),
  keymap.of([indentWithTab]),
  EditorView.theme({
    '&': { fontSize: '13px' },
    '.cm-content': {
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    },
    '.cm-scroller': { overflow: 'auto' },
  }),
]

const editorStyle = computed(() => ({
  height: props.height ?? '480px',
  border: '1px solid #2c313a',
  borderRadius: '4px',
  overflow: 'hidden',
}))
</script>

<template>
  <codemirror
    v-model="value"
    :style="editorStyle"
    :disabled="disabled"
    :placeholder="placeholder"
    :indent-with-tab="false"
    :tab-size="4"
    :autofocus="false"
    :extensions="extensions"
    :disabled-events="readonly ? ['keydown'] : []"
  />
</template>
