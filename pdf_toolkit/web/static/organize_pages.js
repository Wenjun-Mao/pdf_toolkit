document.addEventListener("alpine:init", () => {
    Alpine.data("pageOrganizer", () => ({
        pages: [],
        original: [],
        selected: [],
        history: [],
        dragged: null,
        dragTarget: null,
        previewPage: null,
        busy: false,
        error: "",

        init() {
            this.original = Array.from({ length: Number(this.$el.dataset.pageCount) }, (_, i) => i + 1);
            this.pages = [...this.original];
        },

        get changed() {
            return this.pages.length !== this.original.length || this.pages.some((page, i) => page !== this.original[i]);
        },

        imageUrl(page, size = "thumbnail") {
            return `/tools/organize-pages/${this.$root.dataset.jobId}/pages/${page}?size=${size}`;
        },

        remember() {
            this.history.push({ pages: [...this.pages], selected: [...this.selected] });
            if (this.history.length > 30) this.history.shift();
            this.error = "";
        },

        selectAll(checked) {
            this.selected = checked ? [...this.pages] : [];
        },

        remove(numbers) {
            if (this.busy || !numbers.length) return;
            const removed = new Set(numbers);
            this.remember();
            this.pages = this.pages.filter((page) => !removed.has(page));
            this.selected = this.selected.filter((page) => !removed.has(page));
        },

        move(page, position) {
            if (this.busy || position < 0 || position >= this.pages.length) return;
            const from = this.pages.indexOf(page);
            if (from < 0 || from === position) return;
            this.remember();
            const reordered = [...this.pages];
            reordered.splice(from, 1);
            reordered.splice(position, 0, page);
            this.pages = reordered;
        },

        startDrag(event, page) {
            if (this.busy) {
                event.preventDefault();
                return;
            }
            this.dragged = page;
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", String(page));
        },

        drop(target) {
            if (this.dragged !== null) this.move(this.dragged, this.pages.indexOf(target));
            this.dragged = null;
            this.dragTarget = null;
        },

        undo() {
            if (this.busy || !this.history.length) return;
            const previous = this.history.pop();
            this.pages = previous.pages;
            this.selected = previous.selected;
            this.error = "";
        },

        reset() {
            if (this.busy || !this.changed) return;
            this.remember();
            this.pages = [...this.original];
            this.selected = [];
        },

        openPreview(page) {
            this.previewPage = page;
            this.$nextTick(() => this.$refs.preview.showModal());
        },

        async send(action, fields) {
            if (this.busy) return;
            this.busy = true;
            this.error = "";
            const root = this.$root;
            try {
                const response = await fetch(`/tools/organize-pages/${root.dataset.jobId}/${action}`, {
                    method: "POST", body: fields, credentials: "same-origin",
                });
                if (!response.ok) {
                    const payload = await response.json();
                    this.error = typeof payload.detail === "string" ? payload.detail : "The PDF could not be saved. Please try again.";
                    return;
                }
                const html = await response.text();
                const parent = root.parentElement;
                root.outerHTML = html;
                window.htmx.process(parent);
            } catch {
                this.error = "Could not reach the server. Your edits are still here; try again.";
            } finally {
                this.busy = false;
            }
        },

        save() {
            if (!this.pages.length) {
                this.error = "Keep at least one page before saving.";
                return;
            }
            const fields = new FormData();
            this.pages.forEach((page) => fields.append("page_numbers", String(page)));
            return this.send("submit", fields);
        },

        cancel() {
            return this.send("cancel", new FormData());
        },
    }));
});
