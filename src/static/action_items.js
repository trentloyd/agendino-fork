class ActionItemsManager {
    constructor() {
        this.actionItems = [];
        this.currentFilter = 'active';
        this.currentEditingId = null;
        this.selectedItems = new Set();
        this.editModal = new bootstrap.Modal(document.getElementById('editModal'));
        this.confirmModal = new bootstrap.Modal(document.getElementById('confirmModal'));
        this.pendingAction = null;

        this.initEventListeners();
        this.loadActionItems();
    }

    initEventListeners() {
        document.getElementById('btn-refresh').addEventListener('click', () => {
            this.loadActionItems();
        });

        document.getElementById('btn-show-all').addEventListener('click', () => {
            this.setFilter('all');
        });

        document.getElementById('btn-show-active').addEventListener('click', () => {
            this.setFilter('active');
        });

        document.getElementById('btn-show-archived').addEventListener('click', () => {
            this.setFilter('archived');
        });

        document.getElementById('search-filter').addEventListener('input', (e) => {
            this.filterItems();
        });

        document.getElementById('status-filter').addEventListener('change', (e) => {
            this.filterItems();
        });

        document.getElementById('priority-filter').addEventListener('change', (e) => {
            this.filterItems();
        });

        document.getElementById('save-changes').addEventListener('click', () => {
            this.saveChanges();
        });

        document.getElementById('confirmModalAction').addEventListener('click', () => {
            if (this.pendingAction) {
                this.confirmModal.hide();
                this.pendingAction();
                this.pendingAction = null;
            }
        });

        // Batch action listeners
        document.getElementById('select-all').addEventListener('change', (e) => {
            this.toggleSelectAll(e.target.checked);
        });

        document.getElementById('btn-batch-complete').addEventListener('click', () => {
            this.batchUpdateStatus('completed');
        });

        document.getElementById('btn-batch-archive').addEventListener('click', () => {
            this.batchArchive();
        });

        document.getElementById('btn-batch-delete').addEventListener('click', () => {
            this.batchDelete();
        });

        document.getElementById('btn-clear-selection').addEventListener('click', () => {
            this.clearSelection();
        });
    }

    setFilter(filter) {
        this.currentFilter = filter;

        document.querySelectorAll('#btn-show-all, #btn-show-active, #btn-show-archived')
            .forEach(btn => btn.classList.remove('active'));

        document.getElementById(`btn-show-${filter}`).classList.add('active');

        this.loadActionItems();
    }

    async loadActionItems() {
        try {
            document.getElementById('loading').style.display = 'block';
            document.getElementById('action-items-container').style.display = 'none';
            document.getElementById('empty-state').style.display = 'none';

            const includeArchived = this.currentFilter === 'all' || this.currentFilter === 'archived';
            const response = await fetch(`/api/action-items?include_archived=${includeArchived}`);

            if (!response.ok) {
                throw new Error('Failed to load action items');
            }

            this.actionItems = await response.json();
            this.renderActionItems();
            this.updateStats();

        } catch (error) {
            console.error('Error loading action items:', error);
            this.showError('Failed to load action items');
        } finally {
            document.getElementById('loading').style.display = 'none';
        }
    }

    renderActionItems() {
        const container = document.getElementById('action-items-list');
        container.innerHTML = '';

        let filteredItems = this.getFilteredItems();

        if (filteredItems.length === 0) {
            document.getElementById('empty-state').style.display = 'block';
            document.getElementById('action-items-container').style.display = 'none';
            return;
        }

        document.getElementById('action-items-container').style.display = 'block';
        document.getElementById('empty-state').style.display = 'none';

        filteredItems.forEach(item => {
            const row = this.createActionItemRow(item);
            container.appendChild(row);
        });

        // Add event listeners for checkboxes
        this.attachCheckboxListeners();
    }

    getFilteredItems() {
        let filtered = this.actionItems;

        if (this.currentFilter === 'active') {
            filtered = filtered.filter(item => !item.archived);
        } else if (this.currentFilter === 'archived') {
            filtered = filtered.filter(item => item.archived);
        }

        const searchTerm = document.getElementById('search-filter').value.toLowerCase();
        const statusFilter = document.getElementById('status-filter').value;
        const priorityFilter = document.getElementById('priority-filter').value;

        if (searchTerm) {
            filtered = filtered.filter(item =>
                item.title.toLowerCase().includes(searchTerm) ||
                (item.description && item.description.toLowerCase().includes(searchTerm)) ||
                (item.meeting_title && item.meeting_title.toLowerCase().includes(searchTerm))
            );
        }

        if (statusFilter) {
            filtered = filtered.filter(item => item.status === statusFilter);
        }

        if (priorityFilter) {
            filtered = filtered.filter(item => item.priority === priorityFilter);
        }

        return filtered;
    }

    createActionItemRow(item) {
        const row = document.createElement('tr');
        row.className = item.archived ? 'text-muted' : '';

        const statusIcon = this.getStatusIcon(item.status);
        const priorityBadge = this.getPriorityBadge(item.priority);
        const statusBadge = this.getStatusBadge(item.status);
        const dueDateFormatted = item.due_date ? new Date(item.due_date).toLocaleDateString() : '';
        const meetingDate = item.meeting_date ? new Date(item.meeting_date).toLocaleDateString() : '';

        row.innerHTML = `
            <td class="text-center">
                <input type="checkbox" class="form-check-input item-checkbox" data-id="${item.id}">
            </td>
            <td>
                <div class="fw-bold">${this.escapeHtml(item.title)}</div>
                ${item.description ? `<small class="text-muted">${this.escapeHtml(item.description)}</small>` : ''}
            </td>
            <td>
                ${item.meeting_title ? `<div>${this.escapeHtml(item.meeting_title)}</div>` : ''}
                ${meetingDate ? `<small class="text-muted">${meetingDate}</small>` : ''}
            </td>
            <td>${priorityBadge}</td>
            <td>${statusBadge}</td>
            <td>${item.assigned_to ? this.escapeHtml(item.assigned_to) : ''}</td>
            <td>${dueDateFormatted}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-primary btn-sm" onclick="actionItemsManager.editItem(${item.id})" title="Edit">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${item.archived ?
                        `<button class="btn btn-outline-success btn-sm" onclick="actionItemsManager.unarchiveItem(${item.id})" title="Unarchive">
                            <i class="bi bi-archive"></i>
                        </button>` :
                        `<button class="btn btn-outline-warning btn-sm" onclick="actionItemsManager.archiveItem(${item.id})" title="Archive">
                            <i class="bi bi-archive-fill"></i>
                        </button>`
                    }
                    <button class="btn btn-outline-danger btn-sm" onclick="actionItemsManager.deleteItem(${item.id})" title="Delete">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        `;

        return row;
    }

    getStatusIcon(status) {
        switch(status) {
            case 'completed': return 'bi-check-circle-fill text-success';
            case 'in_progress': return 'bi-clock-fill text-warning';
            default: return 'bi-circle text-muted';
        }
    }

    getPriorityBadge(priority) {
        switch(priority) {
            case 'high': return '<span class="badge bg-danger">High</span>';
            case 'medium': return '<span class="badge bg-warning">Medium</span>';
            case 'low': return '<span class="badge bg-secondary">Low</span>';
            default: return '<span class="badge bg-secondary">Medium</span>';
        }
    }

    getStatusBadge(status) {
        switch(status) {
            case 'completed': return '<span class="badge bg-success">Completed</span>';
            case 'in_progress': return '<span class="badge bg-warning">In Progress</span>';
            case 'pending': return '<span class="badge bg-secondary">Pending</span>';
            default: return '<span class="badge bg-secondary">Pending</span>';
        }
    }

    filterItems() {
        this.renderActionItems();
    }

    updateStats() {
        const total = this.actionItems.length;
        const pending = this.actionItems.filter(item => item.status === 'pending' && !item.archived).length;
        const completed = this.actionItems.filter(item => item.status === 'completed').length;

        document.getElementById('stats-total').textContent = total;
        document.getElementById('stats-pending').textContent = pending;
        document.getElementById('stats-completed').textContent = completed;
    }

    editItem(id) {
        const item = this.actionItems.find(item => item.id === id);
        if (!item) return;

        this.currentEditingId = id;

        document.getElementById('edit-title').value = item.title || '';
        document.getElementById('edit-description').value = item.description || '';
        document.getElementById('edit-priority').value = item.priority || 'medium';
        document.getElementById('edit-status').value = item.status || 'pending';
        document.getElementById('edit-assigned').value = item.assigned_to || '';
        document.getElementById('edit-due-date').value = item.due_date ? item.due_date.split('T')[0] : '';

        this.editModal.show();
    }

    async saveChanges() {
        if (!this.currentEditingId) return;

        const formData = {
            title: document.getElementById('edit-title').value,
            description: document.getElementById('edit-description').value,
            priority: document.getElementById('edit-priority').value,
            status: document.getElementById('edit-status').value,
            assigned_to: document.getElementById('edit-assigned').value,
            due_date: document.getElementById('edit-due-date').value || null
        };

        try {
            const response = await fetch(`/api/action-items/${this.currentEditingId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(formData),
            });

            if (!response.ok) {
                throw new Error('Failed to update action item');
            }

            this.editModal.hide();
            this.loadActionItems();
            this.showSuccess('Action item updated successfully');

        } catch (error) {
            console.error('Error updating action item:', error);
            this.showError('Failed to update action item');
        }
    }

    async archiveItem(id) {
        this.showConfirmation(
            'Archive Action Item',
            'Are you sure you want to archive this action item?',
            async () => {
                try {
                    const response = await fetch(`/api/action-items/${id}/archive`, {
                        method: 'POST'
                    });

                    if (!response.ok) {
                        throw new Error('Failed to archive action item');
                    }

                    this.loadActionItems();
                    this.showSuccess('Action item archived successfully');

                } catch (error) {
                    console.error('Error archiving action item:', error);
                    this.showError('Failed to archive action item');
                }
            }
        );
    }

    async unarchiveItem(id) {
        try {
            const response = await fetch(`/api/action-items/${id}/unarchive`, {
                method: 'POST'
            });

            if (!response.ok) {
                throw new Error('Failed to unarchive action item');
            }

            this.loadActionItems();
            this.showSuccess('Action item unarchived successfully');

        } catch (error) {
            console.error('Error unarchiving action item:', error);
            this.showError('Failed to unarchive action item');
        }
    }

    async deleteItem(id) {
        this.showConfirmation(
            'Delete Action Item',
            'Are you sure you want to permanently delete this action item? This cannot be undone.',
            async () => {
                try {
                    const response = await fetch(`/api/action-items/${id}`, {
                        method: 'DELETE'
                    });

                    if (!response.ok) {
                        throw new Error('Failed to delete action item');
                    }

                    this.loadActionItems();
                    this.showSuccess('Action item deleted successfully');

                } catch (error) {
                    console.error('Error deleting action item:', error);
                    this.showError('Failed to delete action item');
                }
            }
        );
    }

    showConfirmation(title, message, action) {
        document.getElementById('confirmModalTitle').textContent = title;
        document.getElementById('confirmModalMessage').textContent = message;
        this.pendingAction = action;
        this.confirmModal.show();
    }

    showSuccess(message) {
        this.showToast(message, 'success');
    }

    showError(message) {
        this.showToast(message, 'danger');
    }

    showToast(message, type) {
        const toastContainer = document.getElementById('toast-container') || this.createToastContainer();

        const toastId = 'toast-' + Date.now();
        const toastHtml = `
            <div id="${toastId}" class="toast" role="alert">
                <div class="toast-header">
                    <i class="bi bi-${type === 'success' ? 'check-circle' : 'exclamation-triangle'} me-2 text-${type}"></i>
                    <strong class="me-auto">${type === 'success' ? 'Success' : 'Error'}</strong>
                    <button type="button" class="btn-close" data-bs-dismiss="toast"></button>
                </div>
                <div class="toast-body">${message}</div>
            </div>
        `;

        toastContainer.insertAdjacentHTML('beforeend', toastHtml);

        const toast = new bootstrap.Toast(document.getElementById(toastId));
        toast.show();

        document.getElementById(toastId).addEventListener('hidden.bs.toast', function() {
            this.remove();
        });
    }

    createToastContainer() {
        const container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container position-fixed top-0 end-0 p-3';
        document.body.appendChild(container);
        return container;
    }

    escapeHtml(text) {
        const map = {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        };
        return text.replace(/[&<>"']/g, function(m) { return map[m]; });
    }

    // Batch operations methods
    attachCheckboxListeners() {
        document.querySelectorAll('.item-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                const itemId = parseInt(e.target.dataset.id);
                if (e.target.checked) {
                    this.selectedItems.add(itemId);
                } else {
                    this.selectedItems.delete(itemId);
                }
                this.updateSelectionUI();
            });
        });
    }

    toggleSelectAll(checked) {
        this.selectedItems.clear();
        if (checked) {
            const filteredItems = this.getFilteredItems();
            filteredItems.forEach(item => {
                this.selectedItems.add(item.id);
            });
        }

        document.querySelectorAll('.item-checkbox').forEach(checkbox => {
            checkbox.checked = checked;
        });

        this.updateSelectionUI();
    }

    updateSelectionUI() {
        const count = this.selectedItems.size;
        const batchActions = document.getElementById('batch-actions');
        const selectionCount = document.getElementById('selection-count');

        if (count > 0) {
            batchActions.style.display = 'block';
            selectionCount.textContent = `${count} item${count === 1 ? '' : 's'} selected`;
        } else {
            batchActions.style.display = 'none';
        }

        // Update select-all checkbox state
        const selectAllCheckbox = document.getElementById('select-all');
        const allCheckboxes = document.querySelectorAll('.item-checkbox');
        const checkedCheckboxes = document.querySelectorAll('.item-checkbox:checked');

        if (checkedCheckboxes.length === 0) {
            selectAllCheckbox.indeterminate = false;
            selectAllCheckbox.checked = false;
        } else if (checkedCheckboxes.length === allCheckboxes.length) {
            selectAllCheckbox.indeterminate = false;
            selectAllCheckbox.checked = true;
        } else {
            selectAllCheckbox.indeterminate = true;
        }
    }

    clearSelection() {
        this.selectedItems.clear();
        document.querySelectorAll('.item-checkbox').forEach(checkbox => {
            checkbox.checked = false;
        });
        document.getElementById('select-all').checked = false;
        this.updateSelectionUI();
    }

    async batchUpdateStatus(status) {
        if (this.selectedItems.size === 0) return;

        const count = this.selectedItems.size;
        this.showConfirmation(
            'Update Status',
            `Mark ${count} item${count === 1 ? '' : 's'} as ${status}?`,
            async () => {
                const promises = Array.from(this.selectedItems).map(id =>
                    fetch(`/api/action-items/${id}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ status })
                    })
                );

                try {
                    await Promise.all(promises);
                    this.clearSelection();
                    this.loadActionItems();
                    this.showSuccess(`${count} item${count === 1 ? '' : 's'} updated successfully`);
                } catch (error) {
                    console.error('Error updating items:', error);
                    this.showError('Failed to update some items');
                }
            }
        );
    }

    async batchArchive() {
        if (this.selectedItems.size === 0) return;

        const count = this.selectedItems.size;
        this.showConfirmation(
            'Archive Items',
            `Archive ${count} item${count === 1 ? '' : 's'}?`,
            async () => {
                const promises = Array.from(this.selectedItems).map(id =>
                    fetch(`/api/action-items/${id}/archive`, { method: 'POST' })
                );

                try {
                    await Promise.all(promises);
                    this.clearSelection();
                    this.loadActionItems();
                    this.showSuccess(`${count} item${count === 1 ? '' : 's'} archived successfully`);
                } catch (error) {
                    console.error('Error archiving items:', error);
                    this.showError('Failed to archive some items');
                }
            }
        );
    }

    async batchDelete() {
        if (this.selectedItems.size === 0) return;

        const count = this.selectedItems.size;
        this.showConfirmation(
            'Delete Items',
            `Permanently delete ${count} item${count === 1 ? '' : 's'}? This cannot be undone.`,
            async () => {
                const promises = Array.from(this.selectedItems).map(id =>
                    fetch(`/api/action-items/${id}`, { method: 'DELETE' })
                );

                try {
                    await Promise.all(promises);
                    this.clearSelection();
                    this.loadActionItems();
                    this.showSuccess(`${count} item${count === 1 ? '' : 's'} deleted successfully`);
                } catch (error) {
                    console.error('Error deleting items:', error);
                    this.showError('Failed to delete some items');
                }
            }
        );
    }
}

let actionItemsManager;

document.addEventListener('DOMContentLoaded', function() {
    actionItemsManager = new ActionItemsManager();
});