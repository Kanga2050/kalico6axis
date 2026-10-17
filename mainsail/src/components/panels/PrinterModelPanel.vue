<template>
    <panel
        :icon="mdiPrinter3d"
        :title="$t('Panels.PrinterModelPanel.Headline')"
        :collapsible="true"
        card-class="printer-model-panel">
        <template #buttons>
            <v-btn icon tile @click="fullscreen(container)">
                <v-icon>{{ mdiFullscreen }}</v-icon>
            </v-btn>
        </template>
        <v-card-text class="pa-0">
            <div ref="container" class="printer-model-canvas" />
        </v-card-text>
    </panel>
</template>

<script lang="ts">
import Component from 'vue-class-component'
import { Mixins, Ref, Watch } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'
import Panel from '@/components/ui/Panel.vue'
import GCodeViewer from '@sindarius/gcodeviewer'
import type { GCodeViewerInstance } from '@/store/gcodeviewer/types'
import { PrinterModel, ModelConfig, readSource } from '@/components/gcodeviewer/printerModel'
import { mdiFullscreen, mdiPrinter3d } from '@mdi/js'
import { addKeyboardMove, fullscreen } from '@/components/gcodeviewer/viewControls'
import axios from 'axios'
import { escapePath } from '@/plugins/helpers'

// One viewer for the life of the page, like the G-code viewer's: a Babylon
// engine that is disposed and created again breaks the gcodeviewer's shared
// meshes, so the canvas is kept and moved into the panel each time it mounts.
// Sealed so Vue does not make the Babylon scene reactive.
const gl = Object.seal({
    canvas: null as HTMLCanvasElement | null,
    viewer: null as GCodeViewerInstance | null,
    model: null as PrinterModel | null,
    key: '',
    file: '',
})

@Component({ components: { Panel } })
export default class PrinterModelPanel extends Mixins(BaseMixin) {
    mdiPrinter3d = mdiPrinter3d
    mdiFullscreen = mdiFullscreen
    fullscreen = fullscreen

    @Ref() readonly container!: HTMLDivElement

    gl = gl
    resizeObserver: ResizeObserver | null = null
    frame = 0

    get modelConfig(): ModelConfig | null {
        return this.$store.state.printer.model ?? null
    }

    get backgroundColor() {
        return this.$store.state.gui.gcodeViewer?.backgroundColor ?? '#121212'
    }

    get gridColor() {
        return this.$store.state.gui.gcodeViewer?.gridColor ?? '#B3B3B3'
    }

    get showAxes() {
        return this.$store.state.gui.gcodeViewer?.showAxes ?? true
    }

    get bedMinSize() {
        return this.$store.state.printer.toolhead?.axis_minimum ?? null
    }

    get bedMaxSize() {
        return this.$store.state.printer.toolhead?.axis_maximum ?? null
    }

    // the file being printed, shown on the bed as it prints
    get printFile(): string {
        const ps = this.$store.state.printer.print_stats
        return ['printing', 'paused', 'complete'].includes(ps?.state) ? (ps.filename ?? '') : ''
    }

    get filePosition(): number {
        return this.$store.state.printer.virtual_sdcard?.file_position ?? 0
    }

    get liveValues() {
        const printer = this.$store.state.printer
        return [
            printer.motion_report?.live_position,
            printer.motion_report?.live_pose,
            printer.toolhead?.position,
            printer.toolhead?.extra_axes,
        ]
    }

    async mounted() {
        if (!gl.canvas) {
            gl.canvas = document.createElement('canvas')
            gl.canvas.className = 'viewer'
        }
        this.container.appendChild(gl.canvas)
        if (!gl.viewer) {
            const viewer = new GCodeViewer(gl.canvas)
            await viewer.init()
            gl.viewer = viewer
            viewer.setCursorVisiblity(false)
            viewer.setZClipPlane(1000000, -1000000)
            PrinterModel.keepThroughClear(viewer, () => gl.model)
            addKeyboardMove(viewer.scene)
        }
        gl.viewer.setBackgroundColor(this.backgroundColor)
        gl.viewer.bed.setBedColor(this.gridColor)
        gl.viewer.axes.show(this.showAxes)
        this.applyBedSize()
        gl.viewer.bed.buildBed()
        gl.viewer.resize()
        await this.loadModel()
        // the panel may have been closed while the model loaded
        if (!this.container) return
        this.move()
        this.loadPrint()
        this.resizeObserver = new ResizeObserver(() => gl.viewer?.resize())
        this.resizeObserver.observe(this.container)
    }

    beforeDestroy() {
        this.resizeObserver?.disconnect()
        cancelAnimationFrame(this.frame)
    }

    applyBedSize() {
        if (!this.gl.viewer) return
        const bv = this.gl.viewer.bed.buildVolume
        if (this.bedMinSize) [bv.x.min, bv.y.min, bv.z.min] = this.bedMinSize
        if (this.bedMaxSize) [bv.x.max, bv.y.max, bv.z.max] = this.bedMaxSize
    }

    @Watch('modelConfig')
    async loadModel() {
        if (!this.gl.viewer) return
        // the store hands out a new object on status updates; only reload on a real change
        const key = JSON.stringify(this.modelConfig)
        if (key === this.gl.key) return
        this.gl.key = key
        this.gl.model?.dispose()
        this.gl.model = null
        if (!this.modelConfig) return
        const viewerScene = this.gl.viewer.scene
        const model = new PrinterModel(viewerScene, this.apiUrl + '/server/files/config')
        await model.load(this.modelConfig)
        if (key !== this.gl.key || !this.gl.viewer) {
            // a newer config arrived (or the panel closed) while this one loaded
            model.dispose()
            return
        }
        this.gl.model = model
        model.fitCamera()
        viewerScene.executeWhenReady(() => viewerScene.render())
        this.move()
    }

    @Watch('liveValues')
    move() {
        if (!this.gl.model) return
        cancelAnimationFrame(this.frame)
        this.frame = requestAnimationFrame(() => {
            if (!this.gl.model) return
            const printer = this.$store.state.printer
            const values: Record<string, number> = {}
            for (const src of this.gl.model.sources()) {
                const v = readSource(src, printer, null)
                if (v !== null) values[src] = v
            }
            this.gl.model.update(values)
            this.gl.viewer?.scene?.render()
        })
    }

    @Watch('printFile')
    async loadPrint() {
        const viewer = this.gl.viewer
        const file = this.printFile
        if (!viewer || file === this.gl.file) return
        this.gl.file = file
        const text: string = file
            ? await axios
                  .get(this.apiUrl + '/server/files/gcodes/' + escapePath(file), { responseType: 'text' })
                  .then((res) => res.data)
                  .catch(() => '')
            : ''
        if (file !== this.gl.file) return
        if (!text) {
            viewer.clearScene(true)
            return
        }
        await viewer.processFile(text)
        this.showProgress()
    }

    // like the G-code viewer's tracking: the file up to where the printer is
    @Watch('filePosition')
    showProgress() {
        const viewer = this.gl.viewer
        if (!viewer || !this.gl.file) return
        const done = this.$store.state.printer.print_stats?.state === 'complete'
        viewer.gcodeProcessor.updateFilePosition(done ? viewer.fileSize : Math.max(0, this.filePosition - 350))
        viewer.scene?.render()
    }

    @Watch('backgroundColor')
    backgroundColorChanged(value: string) {
        this.gl.viewer?.setBackgroundColor(value)
    }

    @Watch('gridColor')
    gridColorChanged(value: string) {
        this.gl.viewer?.bed.setBedColor(value)
    }
}
</script>

<style scoped>
.printer-model-canvas {
    position: relative;
    width: 100%;
    aspect-ratio: 4 / 3;
}

.printer-model-canvas:fullscreen {
    aspect-ratio: auto;
    height: 100vh;
}

.printer-model-canvas ::v-deep .viewer {
    width: 100%;
    height: 100%;
    display: block;
}
</style>
