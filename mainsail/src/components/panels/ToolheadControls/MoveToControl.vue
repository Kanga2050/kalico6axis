<template>
    <v-container v-if="showCoordinates || showPosition" :class="containerClass">
        <responsive
            :breakpoints="{
                xsmall: (el) => el.width <= 320,
                small: (el) => el.width > 320 && el.width <= 460,
                medium: (el) => el.width > 460 && el.width <= 560,
                large: (el) => el.width > 560,
            }">
            <template #default="{ el }">
                <v-row v-if="showPosition" class="flex-nowrap pb-1">
                    <v-col
                        :class="{
                            'col-5': el.is.small,
                            'col-4': el.is.xsmall || el.is.medium,
                            'col-3': el.is.large,
                        }"
                        class="v-subheader text--secondary mr-2">
                        <v-icon small class="mr-1">{{ mdiCrosshairsGps }}</v-icon>
                        <span v-if="!el.is.xsmall" class="text-no-wrap">
                            {{ $t('Panels.ToolheadControlPanel.Position') }}:&nbsp;
                        </span>
                        <span class="text-no-wrap">{{ displayPositionAbsolute }}</span>
                    </v-col>
                    <v-col
                        v-if="currentProfileName"
                        class="v-subheader text--secondary pl-2 justify-end text-no-wrap text-truncate">
                        <v-icon small class="mr-1">{{ mdiGrid }}</v-icon>
                        <span class="text-no-wrap text-truncate">{{ currentProfileName }}</span>
                    </v-col>
                </v-row>
                <v-row v-if="showCoordinates" dense>
                    <v-col :class="el.is.xsmall ? 'col-12' : 'col-4'">
                        <move-to-input
                            v-model="input.x.pos"
                            :label="livePositions.x"
                            :suffix="'X'"
                            :step="0.01"
                            :current-pos="gcodePositions.x"
                            :readonly="['printing'].includes(printer_state)"
                            :disabled="!xAxisHomed"
                            @submit="sendCmd" />
                    </v-col>
                    <v-col :class="el.is.xsmall ? 'col-12' : 'col-4'">
                        <move-to-input
                            v-model="input.y.pos"
                            :label="livePositions.y"
                            :suffix="'Y'"
                            :step="0.01"
                            :current-pos="gcodePositions.y"
                            :readonly="['printing'].includes(printer_state)"
                            :disabled="!yAxisHomed"
                            @submit="sendCmd" />
                    </v-col>
                    <v-col :class="el.is.xsmall ? 'col-12' : 'col-4'">
                        <move-to-input
                            v-model="input.z.pos"
                            :label="livePositions.z"
                            :suffix="'Z'"
                            :step="0.001"
                            :current-pos="gcodePositions.z"
                            :readonly="['printing'].includes(printer_state)"
                            :disabled="!zAxisHomed"
                            @submit="sendCmd" />
                    </v-col>
                </v-row>
                <v-row v-if="showCoordinates && rotaryAxes.length" dense>
                    <v-col v-for="axis in rotaryAxes" :key="axis" :class="el.is.xsmall ? 'col-12' : 'col-4'">
                        <move-to-input
                            v-model="rotaryInput[axis]"
                            :label="liveRotary(axis)"
                            :suffix="axis"
                            :step="0.01"
                            :current-pos="rotaryPosition(axis)"
                            :readonly="['printing'].includes(printer_state)"
                            :disabled="!xAxisHomed || !yAxisHomed || !zAxisHomed"
                            @submit="sendRotary(axis)" />
                    </v-col>
                </v-row>
            </template>
        </responsive>
    </v-container>
</template>

<script lang="ts">
import { Component, Mixins, Watch } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'
import ControlMixin from '@/components/mixins/control'
import MoveToInput from '@/components/inputs/MoveToInput.vue'
import Responsive from '@/components/ui/Responsive.vue'
import { mdiCrosshairsGps, mdiGrid } from '@mdi/js'

interface MoveToAxisInput {
    pos: string
    valid: boolean
}

interface MoveToInputs {
    x: MoveToAxisInput
    y: MoveToAxisInput
    z: MoveToAxisInput
}

@Component({
    components: { MoveToInput, Responsive },
})
export default class MoveToControl extends Mixins(BaseMixin, ControlMixin) {
    mdiCrosshairsGps = mdiCrosshairsGps
    mdiGrid = mdiGrid

    input: MoveToInputs = {
        x: { pos: '', valid: true },
        y: { pos: '', valid: true },
        z: { pos: '', valid: true },
    }

    @Watch('gcodePositions.x', { immediate: true })
    updatePositionX(newVal: string): void {
        this.input.x.pos = newVal
    }

    @Watch('gcodePositions.y', { immediate: true })
    updatePositionY(newVal: string): void {
        this.input.y.pos = newVal
    }

    @Watch('gcodePositions.z', { immediate: true })
    updatePositionZ(newVal: string): void {
        this.input.z.pos = newVal
    }

    /**
     * Axes positions and positioning mode (G90 / G91)
     */
    get displayPositionAbsolute() {
        return this.positionAbsolute
            ? this.$t('Panels.ToolheadControlPanel.Absolute')
            : this.$t('Panels.ToolheadControlPanel.Relative')
    }

    get positionAbsolute() {
        return this.$store.state.printer.gcode_move?.absolute_coordinates ?? true
    }

    get livePositions() {
        const pos = this.$store.state.printer.motion_report?.live_position ?? [0, 0, 0]
        return {
            x: pos[0]?.toFixed(2) ?? '--',
            y: pos[1]?.toFixed(2) ?? '--',
            z: pos[2]?.toFixed(3) ?? '--',
        }
    }

    get gcodePositions() {
        const pos = this.$store.state.printer.gcode_move?.gcode_position ?? [0, 0, 0]
        return {
            x: pos[0]?.toFixed(2) ?? '--',
            y: pos[1]?.toFixed(2) ?? '--',
            z: pos[2]?.toFixed(3) ?? '--',
        }
    }

    rotaryInput: Record<string, string> = {}

    get rotaryAxes(): string[] {
        const extra = this.$store.state.printer.toolhead?.extra_axes ?? {}
        return Object.keys(extra)
            .filter((a) => ['A', 'B', 'C'].includes(a))
            .sort()
    }

    rotaryPosition(axis: string): string {
        const gm = this.$store.state.printer.gcode_move?.gcode_extra_axes ?? {}
        const th = this.$store.state.printer.toolhead?.extra_axes ?? {}
        return (gm[axis] ?? th[axis] ?? 0).toFixed(2)
    }

    liveRotary(axis: string): string {
        const pose = this.$store.state.printer.motion_report?.live_pose
        const index = ({ A: 3, B: 4, C: 5 } as Record<string, number>)[axis] ?? 3
        return pose ? `${pose[index].toFixed(2)}°` : this.rotaryPosition(axis)
    }

    @Watch('rotaryAxes', { immediate: true })
    syncRotaryInputs(): void {
        for (const axis of this.rotaryAxes) {
            if (!(axis in this.rotaryInput)) this.$set(this.rotaryInput, axis, this.rotaryPosition(axis))
        }
    }

    sendRotary(axis: string): void {
        const pos = this.rotaryInput[axis]
        if (pos === this.rotaryPosition(axis)) return
        // plain G-code: the stock _CLIENT_LINEAR_MOVE macro only passes on X, Y, Z and E
        const gcode = `SAVE_GCODE_STATE NAME=_ui_movement\nG90\nG1 ${axis}${pos} F${this.feedrateXY * 60}\nRESTORE_GCODE_STATE NAME=_ui_movement`
        this.$store.dispatch('server/addEvent', { message: gcode, type: 'command' })
        this.$socket.emit('printer.gcode.script', { script: gcode })
    }

    /**
     * Get currently loaded bed mesh profile name
     */
    get bed_mesh() {
        return this.$store.state.printer.bed_mesh ?? null
    }

    get currentProfileName() {
        return this.bed_mesh?.profile_name ?? ''
    }

    get showPosition() {
        return this.$store.state.gui.view.toolhead.showPosition ?? true
    }

    get showCoordinates() {
        return this.$store.state.gui.view.toolhead.showCoordinates ?? true
    }

    get showControl() {
        return this.$store.state.gui.view.toolhead.showControl ?? true
    }

    get containerClass() {
        return this.showControl ? 'pb-0' : ''
    }

    sendCmd(): void {
        const gcode: string[] = []
        if (!this.existsClientLinearMoveMacro) {
            gcode.push('SAVE_GCODE_STATE NAME=_ui_movement')
            gcode.push('G90')
        }

        if (this.input.z.pos !== this.gcodePositions.z) {
            if (this.existsClientLinearMoveMacro)
                gcode.push(`_CLIENT_LINEAR_MOVE Z=${this.input.z.pos} F=${this.feedrateZ * 60} ABSOLUTE=1`)
            else gcode.push(`G1 Z${this.input.z.pos} F${this.feedrateZ * 60}`)
        }

        if (this.input.x.pos !== this.gcodePositions.x || this.input.y.pos !== this.gcodePositions.y) {
            let xPos = ''
            let yPos = ''

            if (this.existsClientLinearMoveMacro) {
                if (this.input.x.pos !== this.gcodePositions.x) xPos = ` X=${this.input.x.pos}`
                if (this.input.y.pos !== this.gcodePositions.y) yPos = ` Y=${this.input.y.pos}`

                gcode.push(`_CLIENT_LINEAR_MOVE${xPos}${yPos} F=${this.feedrateXY * 60} ABSOLUTE=1`)
            } else {
                if (this.input.x.pos !== this.gcodePositions.x) xPos = ` X${this.input.x.pos}`
                if (this.input.y.pos !== this.gcodePositions.y) yPos = ` Y${this.input.y.pos}`

                gcode.push(`G1${xPos}${yPos} F${this.feedrateXY * 60}`)
            }
        }

        if (!this.existsClientLinearMoveMacro) {
            gcode.push('RESTORE_GCODE_STATE NAME=_ui_movement')
        }

        const gcodeStr = gcode.join('\n')

        if (this.input.x.valid && this.input.y.valid && this.input.z.valid) {
            this.$store.dispatch('server/addEvent', { message: gcodeStr, type: 'command' })
            this.$socket.emit('printer.gcode.script', { script: gcodeStr })
        }

        return
    }
}
</script>
