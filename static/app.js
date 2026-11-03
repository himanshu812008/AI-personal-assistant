document.addEventListener('DOMContentLoaded', () => {
  const form = document.querySelector('#chat-form');
  if (!form) return;

  const input = document.querySelector('#chat-input');
  const messages = document.querySelector('#messages');
  const sendButton = form.querySelector('button[type="submit"]');
  const quickActions = [...document.querySelectorAll('.ai-action')];
  const status = document.querySelector('#ai-status');
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  let pending = false;

  function addMessage(role, text) {
    const item = document.createElement('div');
    item.className = `message ${role}`;
    item.textContent = text;
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
  }

  function setLoading(isLoading) {
    pending = isLoading;
    input.disabled = isLoading;
    sendButton.disabled = isLoading;
    quickActions.forEach((button) => { button.disabled = isLoading; });
    status.textContent = isLoading ? 'Thinking…' : '';
    status.classList.toggle('loading', isLoading);
  }

  async function ask(message, mode = 'chat') {
    if (pending) return;
    const welcome = document.querySelector('#welcome');
    if (welcome) welcome.remove();
    if (mode === 'chat') addMessage('user', message);
    setLoading(true);
    try {
      const response = await fetch('/api/ai', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf || '' },
        body: JSON.stringify({ message, mode })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Unable to contact the AI service.');
      addMessage('assistant', data.answer);
    } catch (error) {
      addMessage('assistant', `Could not complete that request: ${error.message}`);
    } finally {
      setLoading(false);
      input.focus();
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || pending) return;
    input.value = '';
    ask(message);
  });
  quickActions.forEach((button) => button.addEventListener('click', () => ask('', button.dataset.mode)));
});
