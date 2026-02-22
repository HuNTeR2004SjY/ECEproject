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

        try {
            const response = await fetch('/predict', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ subject, body })
            });

            const data = await response.json();

            if (response.ok) {
                // Add to history before displaying results
                addToHistory(subject, data);
                displayResults(data);
            } else {
                alert('Error: ' + (data.error || 'Something went wrong'));
            }
        } catch (error) {
            console.error('Error:', error);
            alert('Failed to connect to the server. Please make sure the app is running.');
        } finally {
            setLoading(false);
        }
    });

    function setLoading(isLoading) {
        submitBtn.disabled = isLoading;
        btnText.style.display = isLoading ? 'none' : 'inline';
        btnLoading.style.display = isLoading ? 'inline-flex' : 'none';
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

    function displayResults(data) {
        // Show results section
        resultsSection.style.display = 'block';

        // Scroll to results
        resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

        // Update Solution
        const solutionText = document.getElementById('solutionText');
        const solutionMethod = document.getElementById('solutionMethod');
        const solutionConfidence = document.getElementById('solutionConfidence');

        if (data.solution) {
            solutionText.innerText = data.solution; // Use innerText to preserve line breaks
            solutionMethod.textContent = data.method === 'direct_retrieval' ? 'Retrieved from Knowledge Base' : 'AI Generated';
            solutionMethod.className = 'solution-badge ' + (data.method === 'direct_retrieval' ? 'badge-retrieved' : 'badge-generated');
            solutionConfidence.textContent = data.confidence + '%';
        } else if (data.escalated) {
            solutionText.textContent = `All ${data.attempts} attempts failed - Escalating to Human Team`;
            solutionMethod.textContent = "Escalated to Human";
            solutionMethod.className = 'solution-badge badge-escalated';
            solutionConfidence.textContent = "0%";
            // Optional: You could add a specific class to solutionText to style it differently
        } else {
            solutionText.textContent = "No solution could be generated for this ticket.";
            solutionMethod.textContent = "Failed";
            solutionConfidence.textContent = "0%";
        }

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
                ? `<span class="tag-score">${data.tag_scores[index]}%</span>`
                : '';

            tagEl.innerHTML = `${tag} ${score}`;
            tagsContainer.appendChild(tagEl);
        });

        // Animate results
        animateResults();
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
