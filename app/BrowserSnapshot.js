() => {
  const MAX_TEXT_LENGTH = 180;
  const MAX_ELEMENTS = 100;
  const MAX_FRAME_COUNT = 100;
  const MAX_TRUNCATE_LENGTH = 10000;

  function getTextContent(element) {
    return element.getAttribute('aria-label') || 
           element.innerText || 
           element.title || 
           element.placeholder || '';
  }

  function getLabel(element) {
    const labels = element.labels || [];
    return labels.length > 0 ? labels[0].innerText : '';
  }

  function getOptions(selectElement) {
    const options = [];
    for (let i = 0; i < Math.min(selectElement.options.length, 40); i++) {
      options.push({
        label: selectElement.options[i].innerText.substring(0, MAX_TEXT_LENGTH),
        value: selectElement.options[i].value
      });
    }
    return options;
  }

  function getSelector(element) {
    const path = [];
    let current = element;
    let depth = 0;
    
    while (current && current.tagName && current !== document.documentElement && depth < 30) {
      let selector = current.tagName.toLowerCase();
      const siblings = Array.from(current.parentNode.children).filter(s => s.tagName === current.tagName);
      if (siblings.length > 1) {
        const index = siblings.indexOf(current) + 1;
        selector += `:nth-of-type(${index})`;
      }
      path.unshift(selector);
      current = current.parentNode;
      depth++;
    }
    
    if (path.length === 0) return '';
    
    const fullPath = path.join(' > ');
    try {
      if (document.querySelectorAll(fullPath).length === 1) {
        return fullPath;
      }
    } catch (e) {}
    
    return '';
  }

  function isVisible(element) {
    const rect = element.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    
    const style = window.getComputedStyle(element);
    if (style.visibility === 'hidden' || style.display === 'none') return false;
    
    return true;
  }

  function getElements() {
    const selectors = [
      'a[href]',
      'button',
      'input:not([type=hidden])',
      'textarea',
      'select',
      '[role=button]',
      '[role=link]',
      '[role=textbox]',
      '[role=checkbox]',
      '[role=combobox]'
    ];
    
    const allElements = [];
    
    selectors.forEach(selector => {
      const elements = document.querySelectorAll(selector);
      elements.forEach(el => {
        if (isVisible(el)) {
          allElements.push(el);
        }
      });
    });
    
    // Remove duplicates based on selector
    const seenSelectors = new Set();
    const uniqueElements = [];
    
    allElements.forEach((el, index) => {
      const selector = getSelector(el);
      if (selector && !seenSelectors.has(selector)) {
        seenSelectors.add(selector);
        uniqueElements.push({ element: el, selector, index });
      }
    });
    
    return uniqueElements.slice(0, MAX_ELEMENTS);
  }

  function getElementData(element, selector, index) {
    const tag = element.tagName.toLowerCase();
    const role = element.getAttribute('role');
    const type = element.getAttribute('type') || (tag === 'button' ? element.type : '');
    const text = getTextContent(element).substring(0, MAX_TEXT_LENGTH);
    const label = getLabel(element).substring(0, MAX_TEXT_LENGTH);
    
    let href = null;
    let disabled = false;
    let options = [];
    
    if (tag === 'a' && element.hasAttribute('href')) {
      href = element.href;
    }
    
    if (element.hasAttribute('disabled') || element.getAttribute('aria-disabled') === 'true') {
      disabled = true;
    }
    
    if (tag === 'select') {
      options = getOptions(element);
    }
    
    return {
      id: index + 1,
      selector,
      tag,
      role,
      type,
      text,
      label,
      href,
      disabled,
      options
    };
  }

  const elements = getElements();
  const elementData = elements.map((el, index) => getElementData(el.element, el.selector, index));
  
  const frames = document.querySelectorAll('iframe').length;
  
  return {
    title: document.title,
    url: window.location.href,
    text: (document.body?.innerText || '').substring(0, MAX_TRUNCATE_LENGTH),
    elements: elementData,
    frames
  };
}
