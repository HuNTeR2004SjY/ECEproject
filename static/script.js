/**
 * TICKET TRIAGE SYSTEM - JAVASCRIPT
 * Handles form submission and displays results
 */

document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('ticketForm');
    const submitBtn = document.getElementById('submitBtn');
    const btnText = submitBtn.querySelector('.btn-text');
    const btnLoading = submitBtn.querySelector('.btn-loading');
    const resultsSection = document.getElementById('results');

    // Ticket history state (session-based)
    // Ticket history state (session-based)
    let ticketHistory = [];

    // Initialize from server data if available
    if (window.SERVER_TICKETS && Array.isArray(window.SERVER_TICKETS)) {
        ticketHistory = window.SERVER_TICKETS.map(t => ({
            id: t.id,
            subject: t.subject,
            type: t.pred_type || 'Unknown',
            status: t.corrected ? 'Resolved' : 'Pending',
            solution: '', // Detailed solution not passed in list view
            confidence: 0,
            timestamp: new Date(t.timestamp ? t.timestamp.replace(' ', 'T') : new Date())
        }));
        console.log('Loaded ' + ticketHistory.length + ' tickets from server history');
    }

    // Fetch real stats from API and update dashboard
    async function fetchDashboardStats() {
        try {
            const response = await fetch('/api/stats');
            if (!response.ok) return;

            const stats = await response.json();

            // Update dashboard elements
            const totalEl = document.getElementById('statTotalTickets');
            const resolvedEl = document.getElementById('statResolvedToday');
            const aiRateEl = document.getElementById('statAiRate');
            const avgResponseEl = document.getElementById('statAvgResponse');
            const weekChangeEl = document.getElementById('statWeekChange');

            if (totalEl) totalEl.textContent = stats.total_tickets.toLocaleString();
            if (resolvedEl) resolvedEl.textContent = stats.resolved_today;
            if (aiRateEl) aiRateEl.textContent = stats.ai_success_rate + '%';
            if (avgResponseEl) avgResponseEl.textContent = stats.avg_response_time + 's';
            if (weekChangeEl) weekChangeEl.textContent = `${stats.week_count} this week`;

        } catch (error) {
            console.error('Failed to fetch stats:', error);
        }
    }

    // Fetch stats on page load
    fetchDashboardStats();

    form.addEventListener('submit', async function (e) {
        e.preventDefault();

        const subject = document.getElementById('subject').value.trim();
        const body = document.getElementById('body').value.trim();

        if (!subject || !body) {
            alert('Please fill in both Subject and Description');
            return;
        }

        // Show loading state
        setLoading(true);

        // Reset results to skeleton state
        showResultsSkeleton();

        let lastTicketId = null;
        let lastSubject = subject;

        try {
            const response = await fetch('/predict/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ subject, body })
            });

            if (!response.ok) {
                const err = await response.json().catch(() => ({ error: 'Server error' }));
                alert('Error: ' + (err.error || 'Something went wrong'));
                setLoading(false);
                return;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const parts = buffer.split('\n\n');
                buffer = parts.pop(); // keep incomplete chunk

                for (const part of parts) {
                    if (!part.trim()) continue;
                    // Parse SSE block
                    const lines = part.split('\n');
                    let eventType = 'message';
                    let eventData = '';
                    for (const line of lines) {
                        if (line.startsWith('event:')) eventType = line.slice(6).trim();
                        else if (line.startsWith('data:')) eventData = line.slice(5).trim();
                    }

                    try {
                        const payload = JSON.parse(eventData);

                        if (eventType === 'triage') {
                            lastTicketId = payload.ticket_id;
                            displayTriageResults(payload);
                            // Show solution skeleton while solver runs
                            setSolutionSolving();

                            const btnStatusText = document.getElementById('btnStatusText');
                            if (btnStatusText) btnStatusText.textContent = 'Checking for Solution...';
                        } else if (eventType === 'solution') {
                            displaySolution(payload);
                        } else if (eventType === 'done') {
                            setLoading(false);
                            // Add to local history now that ticket_id is known
                            const histEntry = {
                                id: payload.ticket_id,
                                subject: lastSubject,
                                type: document.getElementById('typeValue').textContent || 'Unknown',
                                status: document.getElementById('solutionBadge') &&
                                    document.getElementById('solutionBadge').classList.contains('badge-escalated')
                                    ? 'Escalated' : 'Pending',
                                solution: document.getElementById('solutionText').innerText || '',
                                confidence: 0,
                                timestamp: new Date()
                            };
                            ticketHistory.unshift(histEntry);
                            updateHistoryView();
                        } else if (eventType === 'error') {
                            setLoading(false);
                            document.getElementById('solutionText').textContent = 'Error: ' + (payload.error || 'Unknown error');
                            document.getElementById('solutionBadge').textContent = 'Error';
                            document.getElementById('solutionBadge').className = 'solution-badge badge-escalated';
                        }
                    } catch (parseErr) {
                        console.warn('SSE parse error:', parseErr, part);
                    }
                }
            }
        } catch (error) {
            console.error('Stream error:', error);
            alert('Failed to connect to the server. Please make sure the app is running.');
            setLoading(false);
        }
    });

    function setLoading(isLoading) {
        submitBtn.disabled = isLoading;
        btnText.style.display = isLoading ? 'none' : 'inline';
        btnLoading.style.display = isLoading ? 'inline-flex' : 'none';

        if (isLoading) {
            const btnStatusText = document.getElementById('btnStatusText');
            if (btnStatusText) btnStatusText.textContent = 'Triaging Ticket...';
        }
    }

    function addToHistory(subject, data) {
        const ticket = {
            id: data.ticket_id || `WEB-${Date.now().toString(36).toUpperCase()}`,
            subject: subject,
            type: data.type || 'Unknown',
            status: data.escalated ? 'Escalated' : (data.success ? 'Resolved' : 'Pending'),
            solution: data.solution || '',
            confidence: data.confidence || 0,
            timestamp: new Date()
        };
        ticketHistory.unshift(ticket); // Add to beginning
        updateHistoryView();
    }

    function updateHistoryView() {
        const tbody = document.getElementById('historyTableBody');
        const emptyMsg = document.getElementById('historyEmpty');

        if (!tbody) return;

        tbody.innerHTML = '';

        if (ticketHistory.length === 0) {
            if (emptyMsg) emptyMsg.style.display = 'block';
            return;
        }

        if (emptyMsg) emptyMsg.style.display = 'none';

        ticketHistory.forEach(ticket => {
            const row = document.createElement('tr');
            row.className = 'history-row';

            // Status class
            let statusClass = 'status-pending';
            if (ticket.status === 'Resolved') statusClass = 'status-resolved';
            else if (ticket.status === 'Escalated') statusClass = 'status-escalated';

            // Time ago
            const timeAgo = getTimeAgo(ticket.timestamp);

            // Type badge class
            const typeLower = ticket.type.toLowerCase();
            let badgeClass = 'badge-request';
            if (typeLower === 'incident') badgeClass = 'badge-incident';
            else if (typeLower === 'problem') badgeClass = 'badge-problem';

            row.style.cursor = 'pointer';
            row.onclick = () => window.openTicketDetails(ticket.id);

            row.innerHTML = `
                <td>#${ticket.id}</td>
                <td>${escapeHtml(ticket.subject.substring(0, 40))}${ticket.subject.length > 40 ? '...' : ''}</td>
                <td><span class="badge ${badgeClass}">${ticket.type}</span></td>
                <td><span class="${statusClass}">${ticket.status}</span></td>
                <td>${timeAgo}</td>
            `;

            tbody.appendChild(row);
        });
    }

    function getTimeAgo(date) {
        if (!date || isNaN(date.getTime())) return 'Invalid date';

        const seconds = Math.floor((new Date() - date) / 1000);
        if (seconds < 60) return 'Just now';
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes} min${minutes > 1 ? 's' : ''} ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours} hour${hours > 1 ? 's' : ''} ago`;
        return date.toLocaleDateString();
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function submitAnotherTicket() {
        // Clear form
        document.getElementById('subject').value = '';
        document.getElementById('body').value = '';

        // Hide results
        resultsSection.style.display = 'none';

        // Scroll to top of form
        document.querySelector('.ticket-form-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // Bind Submit Another button
    const submitAnotherBtn = document.getElementById('submitAnotherBtn');
    if (submitAnotherBtn) {
        submitAnotherBtn.addEventListener('click', submitAnotherTicket);
    }

    /** Show the results section with skeleton placeholders before any data arrives */
    function showResultsSkeleton() {
        resultsSection.style.display = 'block';
        resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        // Reset classification cards to loading dashes
        document.getElementById('typeValue').textContent = '—';
        document.getElementById('typeConfidence').style.width = '0%';
        document.getElementById('typeConfText').textContent = '0%';
        document.getElementById('priorityValue').textContent = '—';
        document.getElementById('priorityValue').className = 'card-value';
        document.getElementById('priorityConfidence').style.width = '0%';
        document.getElementById('priorityConfText').textContent = '0%';
        document.getElementById('queueValue').textContent = '—';
        document.getElementById('queueConfidence').style.width = '0%';
        document.getElementById('queueConfText').textContent = '0%';
        document.getElementById('tagsContainer').innerHTML = '';

        // Solution card — initial skeleton
        document.getElementById('solutionText').textContent = 'Analysing ticket, please wait...';
        document.getElementById('solutionBadge').textContent = 'Processing';
        document.getElementById('solutionBadge').className = 'solution-badge badge-processing';
        document.getElementById('solutionConfidence').textContent = '—';
        document.getElementById('solutionAttempts').textContent = '—';
    }

    /** Show "Solving…" skeleton in solution card after triage is done */
    function setSolutionSolving() {
        const solutionText = document.getElementById('solutionText');
        const badge = document.getElementById('solutionBadge');
        solutionText.innerHTML = `
            <span class="solving-pulse">🤖 Finding solution</span>
            <span class="solving-dots"><span>.</span><span>.</span><span>.</span></span>
        `;
        badge.textContent = 'Solving';
        badge.className = 'solution-badge badge-solving';
        document.getElementById('solutionConfidence').textContent = '—';
        document.getElementById('solutionAttempts').textContent = '—';
    }

    /** Display ONLY the triage classification cards (called as soon as triage event arrives) */
    function displayTriageResults(data) {
        // Update Type
        document.getElementById('typeValue').textContent = data.type;
        document.getElementById('typeConfidence').style.width = data.type_confidence + '%';
        document.getElementById('typeConfText').textContent = data.type_confidence + '%';

        // Update Priority with color
        const priorityValue = document.getElementById('priorityValue');
        priorityValue.textContent = data.priority;
        priorityValue.className = 'card-value priority-' + data.priority.toLowerCase();
        document.getElementById('priorityConfidence').style.width = data.priority_confidence + '%';
        document.getElementById('priorityConfText').textContent = data.priority_confidence + '%';

        // Update Queue
        document.getElementById('queueValue').textContent = data.queue;
        document.getElementById('queueConfidence').style.width = data.queue_confidence + '%';
        document.getElementById('queueConfText').textContent = data.queue_confidence + '%';

        // Update Tags
        const tagsContainer = document.getElementById('tagsContainer');
        tagsContainer.innerHTML = '';
        data.tags.forEach((tag, index) => {
            const tagEl = document.createElement('span');
            tagEl.className = 'tag';
            const score = data.tag_scores && data.tag_scores[index]
                ? `<span class="tag-score">${data.tag_scores[index]}%</span>` : '';
            tagEl.innerHTML = `${tag} ${score}`;
            tagsContainer.appendChild(tagEl);
        });

        // Animate classification cards
        animateResults();
    }

    /** Display ONLY the solution section (called when solution event arrives) */
    function displaySolution(data) {
        const solutionText = document.getElementById('solutionText');
        const solutionBadge = document.getElementById('solutionBadge');
        const solutionConfidence = document.getElementById('solutionConfidence');
        const solutionAttempts = document.getElementById('solutionAttempts');

        if (data.solution) {
            solutionBadge.style.display = 'inline-block';
            solutionText.innerText = data.solution;
            solutionBadge.textContent = data.method === 'direct_retrieval' ? 'Retrieved from Knowledge Base' : 'AI Solution';
            solutionBadge.className = 'solution-badge ' + (data.method === 'direct_retrieval' ? 'badge-retrieved' : 'badge-generated');
            solutionConfidence.textContent = data.confidence + '%';
        } else if (data.escalated) {
            solutionBadge.style.display = 'inline-block';
            solutionText.textContent = `All ${data.attempts} attempts failed — Escalating to Human Team`;
            solutionBadge.textContent = 'Escalated to Human';
            solutionBadge.className = 'solution-badge badge-escalated';
            solutionConfidence.textContent = '0%';
        } else {
            solutionBadge.style.display = 'inline-block';
            solutionText.textContent = 'No solution could be generated for this ticket.';
            solutionBadge.textContent = 'Failed';
            solutionBadge.className = 'solution-badge badge-escalated';
            solutionConfidence.textContent = '0%';
        }
        solutionAttempts.textContent = data.attempts || 1;

        // Pulse-in animation for solution card
        const card = solutionText.closest('.solution-card');
        if (card) {
            card.classList.remove('solution-arrived');
            void card.offsetWidth;
            card.classList.add('solution-arrived');
        }
    }

    function animateResults() {
        // Re-trigger animation by removing and re-adding the element
        resultsSection.classList.remove('animate');
        void resultsSection.offsetWidth; // Trigger reflow
        resultsSection.classList.add('animate');

        // Animate confidence bars
        const confidenceFills = document.querySelectorAll('.confidence-fill');
        confidenceFills.forEach(fill => {
            const width = fill.style.width;
            fill.style.width = '0%';
            setTimeout(() => {
                fill.style.width = width;
            }, 100);
        });
    }

    // Navigation Logic
    const navItems = document.querySelectorAll('.nav-item');
    const views = document.querySelectorAll('.view-section');
    const pageTitle = document.getElementById('pageTitle');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            // Remove active class from all nav items
            navItems.forEach(nav => nav.classList.remove('active'));
            // Add active class to clicked item
            item.classList.add('active');

            // Switch view
            const viewId = item.getAttribute('data-view');
            const viewTitle = item.textContent.trim();

            // Hide all views
            views.forEach(view => {
                view.style.display = 'none';
                view.classList.remove('active');
            });

            // Show selected view
            const selectedView = document.getElementById(viewId);
            if (selectedView) {
                selectedView.style.display = 'block';
                setTimeout(() => selectedView.classList.add('active'), 10); // Fade in
            }

            // Update page title
            if (pageTitle) pageTitle.textContent = viewTitle;

            // Refresh history view when navigating to it
            if (viewId === 'history-view') {
                updateHistoryView();
            }

            // Refresh stats when navigating to dashboard
            if (viewId === 'dashboard-view') {
                fetchDashboardStats();
            }
        });
    });

    // Add Copy function to window scope
    window.copySolution = function () {
        const text = document.getElementById('solutionText').innerText;
        navigator.clipboard.writeText(text).then(() => {
            const btn = document.querySelector('.copy-btn');
            const originalText = btn.textContent;
            btn.textContent = 'Copied!';
            setTimeout(() => {
                btn.textContent = originalText;
            }, 2000);
        });
    };
    // Check for latest ticket status
    async function checkLatestTicket() {
        try {
            const response = await fetch('/api/user/latest-ticket');
            if (response.status === 401) return; // Not logged in
            if (!response.ok) return;

            const ticket = await response.json();
            if (ticket) {
                // Determine if we should show it (maybe check local storage if already seen?)
                // For now, let's just log it or maybe subtle notification
                // openTicketDetails(ticket.id); // Valid for demo
            }
        } catch (error) {
            console.error('Error fetching latest ticket:', error);
        }
    }

    window.openTicketDetails = async function (ticketId) {
        const modal = document.getElementById('ticketStatusModal');
        const messagesContainer = document.getElementById('chatMessages');

        if (!modal) return;

        // Show modal immediately with loading state
        modal.style.display = 'flex';
        messagesContainer.innerHTML = '<div class="logger-loading">Loading conversation...</div>';

        // Setup Close Handlers
        const closeBtn = modal.querySelector('.close-modal');
        const close = () => { modal.style.display = 'none'; };
        if (closeBtn) closeBtn.onclick = close;
        window.onclick = (e) => { if (e.target == modal) close(); };

        try {
            const response = await fetch(`/api/ticket/${ticketId}/details`);
            if (!response.ok) throw new Error('Failed to fetch details');

            const data = await response.json();
            const ticket = data.ticket;
            const interactions = data.interactions;

            // Render Header Info
            document.getElementById('modalTicketId').textContent = ticket.id;
            document.getElementById('modalSubject').textContent = ticket.subject;
            document.getElementById('modalStatus').textContent = ticket.corrected ? 'Resolved' : 'Pending';

            // Render Chat
            messagesContainer.innerHTML = '';

            // If no interactions, show initial body?
            // app.py now saves initial body as interaction, so interactions shouldn't be empty if created via new system.
            // For old tickets, we might need fallback.

            if (interactions.length === 0) {
                // Fallback for legacy tickets
                renderMessage(messagesContainer, 'user', ticket.body, ticket.timestamp);
                // We don't have the solution text easily available for legacy unless we re-predict or allow "Reply" to trigger it.
                // Let's just show body.
            } else {
                interactions.forEach(msg => {
                    renderMessage(messagesContainer, msg.sender, msg.message, msg.timestamp);
                });
            }

            scrollToBottom();

            // Setup Reply Input
            const sendBtn = document.getElementById('sendReplyBtn');
            const input = document.getElementById('chatInput');

            // Remove old listeners (cloning is a hacky way)
            const newBtn = sendBtn.cloneNode(true);
            sendBtn.parentNode.replaceChild(newBtn, sendBtn);

            newBtn.onclick = () => sendReply(ticketId);

            // Enter key to send
            input.onkeydown = (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    sendReply(ticketId);
                }
            };

            // Setup Resolve Button
            const resolveBtn = document.getElementById('resolveTicketBtn');
            if (resolveBtn) {
                // If already resolved, hide or disable
                if (ticket.corrected) {
                    resolveBtn.style.display = 'none';
                } else {
                    resolveBtn.style.display = 'block';
                    // Remove old listeners
                    const newResolveBtn = resolveBtn.cloneNode(true);
                    resolveBtn.parentNode.replaceChild(newResolveBtn, resolveBtn);
                    newResolveBtn.onclick = () => resolveTicket(ticketId);
                }
            }

        } catch (error) {
            messagesContainer.innerHTML = `<div class="error-msg">Error: ${error.message}</div>`;
        }
    };

    async function resolveTicket(ticketId) {
        if (!confirm('Are you sure you want to mark this ticket as resolved? This will clear the conversation history to save space.')) return;

        const resolveBtn = document.getElementById('resolveTicketBtn');
        if (resolveBtn) resolveBtn.disabled = true;

        try {
            const response = await fetch('/validate-solution', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ ticket_id: ticketId, is_valid: true, feedback: 'Resolved by user' })
            });

            if (!response.ok) throw new Error('Resolution failed');

            const data = await response.json();

            // Show success message
            alert('Ticket resolved and data cleaned up.');

            // Close modal and refresh
            document.getElementById('ticketStatusModal').style.display = 'none';
            // Reload history/dashboard if needed? 
            // Ideally we refresh the list. 
            // But for now just close.
            if (location.reload) location.reload(); // Simple refresh to see status update

        } catch (error) {
            alert('Error: ' + error.message);
            if (resolveBtn) resolveBtn.disabled = false;
        }
    }

    function renderMessage(container, sender, text, timestamp) {
        const bubble = document.createElement('div');
        bubble.className = `chat-bubble ${sender}`;

        // Convert newlines to breaks
        const formattedText = text.replace(/\n/g, '<br>');

        bubble.innerHTML = `
            <div class="chat-text">${formattedText}</div>
            <div class="chat-meta">${new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
        `;

        container.appendChild(bubble);
    }

    function scrollToBottom() {
        const container = document.getElementById('chatMessages');
        container.scrollTop = container.scrollHeight;
    }

    async function sendReply(ticketId) {
        const input = document.getElementById('chatInput');
        const message = input.value.trim();
        const loading = document.getElementById('chatLoading');

        if (!message) return;

        input.value = '';
        input.disabled = true;
        loading.style.display = 'block';

        // Optimistic UI update
        const container = document.getElementById('chatMessages');
        renderMessage(container, 'user', message, new Date());
        scrollToBottom();

        try {
            const response = await fetch(`/api/ticket/${ticketId}/reply`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message })
            });

            if (!response.ok) throw new Error('Reply failed');

            const data = await response.json();

            // Append AI response
            renderMessage(container, 'ai', data.reply, new Date());
            scrollToBottom();

        } catch (error) {
            alert('Failed to send reply: ' + error.message);
        } finally {
            input.disabled = false;
            input.focus();
            loading.style.display = 'none';
        }
    }

    // Call on load
    checkLatestTicket();
});
