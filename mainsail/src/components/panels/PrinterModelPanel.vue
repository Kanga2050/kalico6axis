<template>
    <panel
        v-if="socketIsConnected"
        :icon="mdiPrinter3d"
        :title="$t('Panels.PrinterModelPanel.Headline')"
        :collapsible="true"
        card-class="printer-model-panel">
        <v-card-text class="pa-0">
            <div ref="container" class="printer-model-canvas">
                <canvas ref="canvas" class="viewer" />
            </div>
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
import { mdiPrinter3d } from '@mdi/js'

@Component({ components: { Panel } })
export default class PrinterModelPanel extends Mixins(BaseMixin) {
    mdiPrinter3d = mdiPrinter3d

    @Ref() readonly canvas!: HTMLCanvasElement
    @Ref() readonly container!: HTMLDivElement

    // sealed so Vue does not make the Babylon scene reactive
    gl = Object.seal({ viewer: null as GCodeViewerInstance | null, model: null as PrinterModel | null, key: '' })
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
        const viewer = new GCodeViewer(this.canvas)
        await viewer.init()
        this.gl.viewer = viewer
        this.gl.viewer.setBackgroundColor(this.backgroundColor)
        this.gl.viewer.bed.setBedColor(this.gridColor)
        this.gl.viewer.setCursorVisiblity(false)
        this.gl.viewer.setZClipPlane(1000000, -1000000)
        this.gl.viewer.axes.show(this.showAxes)
        this.applyBedSize()
        this.gl.viewer.bed.buildBed()
        await this.loadModel()
        this.resizeObserver = new ResizeObserver(() => this.gl.viewer?.resize())
        this.resizeObserver.observe(this.container)
    }

    beforeDestroy() {
        this.resizeObserver?.disconnect()
        this.gl.model?.dispose()
        this.gl.viewer?.scene?.getEngine()?.dispose()
        this.gl.viewer = null
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
        const key = JSON.stringify(this.modelConfig)
        if (key === this.gl.key && this.gl.model) return
        this.gl.key = key
        this.gl.model?.dispose()
        this.gl.model = null
        if (!this.modelConfig) return
        const viewerScene = this.gl.viewer.scene
        const model = new PrinterModel(viewerScene, this.apiUrl + '/server/files/config')
        await model.load(this.modelConfig)
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

.viewer {
    width: 100%;
    height: 100%;
    display: block;
}
</style>
