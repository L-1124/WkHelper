(args) => {
    const buttons = [...document.querySelectorAll('button, [role="button"]')];
    const button = buttons.find(el =>
        ['开始答题', '开始作答', '开始考试', 'Start Exam', 'Start'].includes(el.innerText.trim()) &&
        !el.disabled && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
    if (!button) return false;
    button.click();
    return true;
}
