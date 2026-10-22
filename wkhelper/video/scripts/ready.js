(args) => {
    const v = document.querySelector('video');
    if (!v) return { found: false, ready: false };
    v.muted = true;
    if (v.paused && v.readyState === 0) {
        try { v.play().catch(() => {}); } catch(e) {}
    }
    const hasData = v.readyState >= 2;
    const hasDuration = !isNaN(v.duration) && v.duration > 0;
    return {
        found: true,
        ready: Boolean(hasData && hasDuration),
        duration: hasDuration ? v.duration : 0,
        readyState: v.readyState,
        videoWidth: v.videoWidth || 0,
        videoHeight: v.videoHeight || 0
    };
}
