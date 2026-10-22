async (args) => {
    const pid = String(args.pid);
    const keys = args.keys;
    const mode = args.mode;

    const norm = (v) => String(v == null ? '' : v).trim().toUpperCase();

    const findInstance = (root) => {
        const stack = [root];
        while (stack.length) {
            const cur = stack.pop();
            const item = cur.$props ? cur.$props.item : null;
            if (item && item.problem_id !== undefined && String(item.problem_id) === pid) return cur;
            const children = cur.$children || [];
            for (let i = 0; i < children.length; i += 1) stack.push(children[i]);
        }
        return null;
    };

    const roots = Array.from(document.querySelectorAll('*')).filter((el) => el.__vue__);
    let target = null;
    for (const el of roots) {
        const vm = el.__vue__;
        if (!vm) continue;
        target = findInstance(vm.$root);
        if (target) break;
    }
    if (!target) return {success: false, error: '未找到题目 ' + pid + ' 的页面组件'};

    const item = target.$props.item;
    const el = target.$el;
    const type = Number(item.ProblemType);
    const tick = () => new Promise((resolve) => target.$nextTick(resolve));

    const setField = (node, value) => {
        node.scrollIntoView({block: 'center'});
        node.focus();
        if (node.tagName === 'TEXTAREA' || node.tagName === 'INPUT') {
            const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, value);
        } else {
            node.innerText = value;
        }
        node.dispatchEvent(new Event('input', {bubbles: true}));
        node.dispatchEvent(new Event('change', {bubbles: true}));
        node.dispatchEvent(new Event('blur', {bubbles: true}));
    };

    // UEditor 等富文本编辑器的可编辑区位于 iframe 内，必须跨文档查找
    const collectFields = (root) => {
        const selector = 'textarea, input[type="text"], [contenteditable="true"], .ql-editor';
        const found = Array.from(root.querySelectorAll(selector));
        Array.from(root.querySelectorAll('iframe')).forEach((frame) => {
            let doc = null;
            try { doc = frame.contentDocument; } catch (e) { return; }
            if (!doc || !doc.body) return;
            if (doc.body.isContentEditable) found.push(doc.body);
            found.push(...Array.from(doc.querySelectorAll(selector)));
        });
        return found;
    };

    if (mode === 'text' || type === 4 || type === 5) {
        const fields = collectFields(el);
        if (!fields.length) return {success: false, error: '题目 ' + pid + ' 未找到可输入控件'};
        if (type === 4 && keys.length > fields.length) {
            return {success: false, error: '题目 ' + pid + ' 填空数量与答案数量不匹配'};
        }
        if (type === 4 && keys.length < fields.length) {
            return {success: false, error: '题目 ' + pid + ' 有 ' + fields.length + ' 个空，但只提供了 ' + keys.length + ' 个答案'};
        }
        if (type === 4) {
            fields.forEach((field, index) => setField(field, keys[index] ?? ''));
        } else {
            setField(fields[0], keys.join('\n'));
        }
        const readField = (node) => (node.tagName === 'TEXTAREA' || node.tagName === 'INPUT' ? node.value : node.innerText);
        const expected = type === 4 ? keys : [keys.join('\n')];
        const written = (type === 4 ? fields.map(readField) : [readField(fields[0])]).map(norm);
        const verify = expected.map(norm);
        const landed = verify.every((value, index) => written[index] === value);
        if (!landed) {
            return {success: false, error: '题目 ' + pid + ' 输入内容未写入页面控件', written: written};
        }
        await tick();
        const blanks = Array.isArray(item.answerArr) ? item.answerArr.map(norm) : [];
        return {
            success: true,
            mode: 'input',
            problem_type: type,
            filled: type === 4 ? Math.min(keys.length, fields.length) : 1,
            actual: blanks,
        };
    }

    const labels = Array.from(el.querySelectorAll('label.el-radio, label.el-checkbox'));
    if (!labels.length) return {success: false, error: '题目 ' + pid + ' 未找到选项控件'};
    const optionOf = (label) => {
        const input = label.querySelector('input[type="radio"], input[type="checkbox"]');
        const value = norm(input ? input.value : '');
        const text = norm((label.querySelector('.radioInput, .checkboxInput') || label).innerText);
        return {label: label, id: value || text, text: text, value: value};
    };
    const checked = (label) => {
        const input = label.querySelector('input[type="radio"], input[type="checkbox"]');
        return !!(input && input.checked);
    };

    const options = labels.map(optionOf);
    // 判断题的选项键是 true/false，界面显示“正确/错误”，必须按选项键匹配
    const matches = (option, key) => option.value === key || option.text === key;
    const wanted = keys.map(norm);
    const missing = wanted.filter((key) => !options.some((option) => matches(option, key)));
    if (missing.length) return {success: false, error: '考场页面未找到选项: ' + missing.join(',')};
    const expected = wanted.map((key) => options.find((option) => matches(option, key)).id);

    // 点击可能触发组件重渲染，重新查询实时节点并等待渲染完成，避免后续点击落在已卸载的 label 上
    const liveOptions = () => Array.from(el.querySelectorAll('label.el-radio, label.el-checkbox')).map(optionOf);
    let clicked = 0;
    const clickOption = async (key) => {
        const option = liveOptions().find((item) => matches(item, key));
        if (!option) return false;
        if (checked(option.label)) return true;
        option.label.scrollIntoView({block: 'center'});
        option.label.click();
        clicked += 1;
        await tick();
        return true;
    };
    if (type === 1) {
        await clickOption(wanted[0]);
    } else {
        for (const key of wanted) {
            if (!await clickOption(key)) return {success: false, error: '考场页面未找到选项: ' + key};
        }
        // 取消勾选不在答案里的多余选项
        for (const key of liveOptions().map((item) => item.id)) {
            if (wanted.includes(key)) continue;
            const extra = liveOptions().find((item) => item.id === key);
            if (extra && checked(extra.label)) {
                extra.label.click();
                clicked += 1;
                await tick();
            }
        }
    }

    const selected = liveOptions().filter((option) => checked(option.label)).map((option) => option.id);
    const matched = selected.length === expected.length && expected.every((key) => selected.includes(key));
    return {
        success: matched,
        mode: 'click',
        problem_type: type,
        clicked_count: clicked,
        selected: selected,
        error: matched ? null : '选项点击后状态不符: 期望 ' + expected.join(',') + ' 实际 ' + (selected.join(',') || '空'),
    };
}
