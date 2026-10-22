(args) => {
    const selectors = [
        '.center-play-btn',
        '.xt_video_player_common',
        '.vjs-control-bar',
        '.player-mask',
        '.play-btn',
        '.vjs-big-play-button',
        '.video-control'
    ];
    for (const sel of selectors) {
        document.querySelectorAll(sel).forEach(el => { el.style.display = 'none'; });
    }
    const v = document.querySelector('video');
    if (!v) return null;
    const rect = v.getBoundingClientRect();
    return {
        x: rect.x + window.scrollX,
        y: rect.y + window.scrollY,
        width: rect.width,
        height: rect.height
    };
}
