async (args) => {
    const v = document.querySelector('video');
    if (!v) return { ok: false, error: 'no_video' };
    v.muted = true;

    const targetTime = Number(args.timestamp);
    const targetWidth = Number(args.width || 0);
    const targetHeight = Number(args.height || 0);
    const qualityRatio = Number(args.quality_ratio || 0.85);

    let seekTimedOut = false;
    if (Math.abs(v.currentTime - targetTime) > 0.05) {
        seekTimedOut = await new Promise((resolve) => {
            let settled = false;
            const onSeeked = () => {
                if (!settled) {
                    settled = true;
                    cleanup();
                    resolve(false);
                }
            };
            const timer = setTimeout(() => {
                if (!settled) {
                    settled = true;
                    cleanup();
                    resolve(true);
                }
            }, 5000);
            function cleanup() {
                v.removeEventListener('seeked', onSeeked);
                clearTimeout(timer);
            }
            v.addEventListener('seeked', onSeeked, { once: true });
            v.currentTime = targetTime;
        });
    }

    if (seekTimedOut) {
        return {
            ok: false,
            error: 'seek_timeout',
            actualTime: v.currentTime
        };
    }

    const w = targetWidth || v.videoWidth || 1280;
    const h = targetHeight || v.videoHeight || 720;

    try {
        const canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(v, 0, 0, w, h);
        const dataUrl = canvas.toDataURL('image/jpeg', qualityRatio);
        if (!dataUrl || dataUrl.length < 50) {
            throw new Error('empty_data_url');
        }
        return {
            ok: true,
            method: 'canvas',
            dataUrl: dataUrl,
            width: w,
            height: h,
            actualTime: v.currentTime
        };
    } catch (err) {
        return {
            ok: false,
            fallback_required: true,
            error: err.name || String(err),
            width: w,
            height: h,
            actualTime: v.currentTime
        };
    }
}
