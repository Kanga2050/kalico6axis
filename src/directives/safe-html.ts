import type { FunctionDirective } from 'vue'
import DOMPurify from 'dompurify'

DOMPurify.addHook('uponSanitizeAttribute', (currentNode, hookEvent) => {
  if (
    currentNode.tagName === 'A' &&
    hookEvent.attrName === 'target' &&
    hookEvent.attrValue === '_blank'
  ) {
    hookEvent.forceKeepAttr = true
  }
})

const safeHtml: FunctionDirective<HTMLElement> = (el, binding) => {
  if (binding.value !== binding.oldValue) {
    el.innerHTML = DOMPurify.sanitize(binding.value)
  }
}

export default safeHtml
