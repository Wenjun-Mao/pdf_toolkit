(function () {
    const optimizedFields = [
        ["strength", "optimizedStrength"],
        ["white_point", "optimizedWhitePoint"],
        ["contrast", "optimizedContrast"],
        ["dpi_cap", "optimizedDpiCap"],
        ["jpeg_quality", "optimizedJpegQuality"],
    ];
    const applyStatus = "Optimized settings applied. Preview refresh requested.";

    window.applyOptimizedScanSettingsToForm = function (proposal, form, refreshPreview) {
        if (!proposal || !form) {
            return;
        }

        optimizedFields.forEach(([fieldName, datasetKey]) => {
            const input = form.querySelector(`[name="${fieldName}"]`);
            const value = proposal.dataset[datasetKey];
            if (!input || value === undefined) {
                return;
            }
            input.value = value;
        });

        const state = proposal.querySelector("[data-optimization-state]");
        if (state) {
            state.textContent = applyStatus;
        }

        if (typeof refreshPreview === "function") {
            refreshPreview(form);
        }
    };
}());
