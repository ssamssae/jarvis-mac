// A momentary start button. OFF never runs a command; busy/repeated ONs are ignored.
export class StartGate {
    constructor(run, { now = () => Date.now(), cooldownMs = 5000 } = {}) {
        this.run = run;
        this.now = now;
        this.cooldownMs = cooldownMs;
        this.busy = false;
        this.last = -Infinity;
    }

    async accept(value) {
        if (value !== true || this.busy || this.now() - this.last < this.cooldownMs) return false;
        this.busy = true;
        this.last = this.now();
        try {
            await this.run();
            return true;
        } finally {
            this.busy = false;
        }
    }
}
