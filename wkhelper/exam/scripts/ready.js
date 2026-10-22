(args) => {
    const eid = String(args && args.eid != null ? args.eid : '');
    if (location.pathname !== '/exam/' + eid) return false;
    return [...document.querySelectorAll('*')].some(el => {
        const item = el.__vue__ && el.__vue__.$props && el.__vue__.$props.item;
        return item && item.problem_id !== undefined;
    });
}
