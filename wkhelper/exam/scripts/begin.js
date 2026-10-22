(args) => {
    const allButtons = [...document.querySelectorAll('button, [role="button"], .el-button')].filter(el =>
        !el.disabled && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
    );

    // 1. 优先查找可见确认弹窗里的“开始/确定”按钮（通过同级或页面存在“暂不开始/取消”识别确认弹窗）
    const hasCancel = allButtons.some(el => ['暂不开始', '取消', 'Cancel'].includes(el.innerText.trim()));
    if (hasCancel) {
        const confirmBtn = allButtons.find(el => ['开始', '确定', '确认', 'Start', 'Confirm'].includes(el.innerText.trim()));
        if (confirmBtn) {
            confirmBtn.click();
            return true;
        }
    }

    // 2. 否则查找封面的开始答题入口按钮
    const entryButton = allButtons.find(el =>
        ['开始答题', '开始作答', '开始考试', 'Start Exam'].includes(el.innerText.trim())
    );
    if (entryButton) {
        entryButton.click();
        return true;
    }

    return false;
}
