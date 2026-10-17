<template>
    <div v-if="rotaryAxes.length">
        <v-row v-for="axis in rotaryAxes" :key="axis" dense>
            <v-col class="text-center">
                <v-item-group class="_btn-group row no-gutters">
                    <v-btn
                        v-for="steps of stepsSorted"
                        :key="axis + '-' + steps"
                        :disabled="disabled"
                        class="btnMinWidthAuto col btnGroup"
                        @click="doRotaryMove(axis, -steps, false)">
                        <span class="body-2">–{{ steps }}°</span>
                    </v-btn>
                    <v-btn
                        :disabled="disabled"
                        color="primary"
                        class="font-weight-bold btnHomeAxis btnGroup"
                        :title="axis + ' to 0°'"
                        @click="doRotaryMove(axis, 0, true)">
                        {{ axis }}
                    </v-btn>
                    <v-btn
                        v-for="steps of stepsSortedReverse"
                        :key="axis + '+' + steps"
                        :disabled="disabled"
                        class="btnMinWidthAuto col btnGroup"
                        @click="doRotaryMove(axis, steps, false)">
                        <span class="body-2">+{{ steps }}°</span>
                    </v-btn>
                </v-item-group>
            </v-col>
        </v-row>
    </div>
</template>

<script lang="ts">
import { Component, Mixins } from 'vue-property-decorator'
import BaseMixin from '@/components/mixins/base'
import ControlMixin from '@/components/mixins/control'

// jog rows for the rotary axes (A, B, C) that the kinematics provides;
// Kalico works out how every motor moves for each rotation
@Component
export default class RotaryControl extends Mixins(BaseMixin, ControlMixin) {
    steps = [1, 15, 90]

    get disabled(): boolean {
        const homed = this.$store.state.printer.toolhead?.homed_axes ?? ''
        return ['printing'].includes(this.printer_state) || !homed.includes('xyz')
    }

    get stepsSorted() {
        return [...this.steps].sort((a, b) => b - a)
    }

    get stepsSortedReverse() {
        return [...this.steps].sort((a, b) => a - b)
    }
}
</script>

<style scoped>
.btnHomeAxis {
    width: 36px;
    min-width: 36px !important;
}

.btnGroup {
    height: 28px !important;
}

.btnMinWidthAuto {
    min-width: auto !important;
}

._btn-group {
    border-radius: 4px;
    display: inline-flex;
    flex-wrap: nowrap;
    max-width: 100%;
    min-width: 100%;
    width: 100%;

    .v-btn {
        border-radius: 0;
        border-color: rgba(255, 255, 255, 0.12);
        border-style: solid;
        border-width: thin;
        box-shadow: none;
        height: 28px;
        opacity: 0.8;
        min-width: auto !important;
    }

    .v-btn:first-child {
        border-top-left-radius: inherit;
        border-bottom-left-radius: inherit;
    }

    .v-btn:last-child {
        border-top-right-radius: inherit;
        border-bottom-right-radius: inherit;
    }

    .v-btn:not(:first-child) {
        border-left-width: 0;
    }
}

html.theme--light ._btn-group .v-btn {
    border-color: rgba(0, 0, 0, 0.12);
}
</style>
