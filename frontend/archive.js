function archiveOrderCard(o) {
  return renderOrderCard(o, {
    editable: false,
    showUnarchiveButton: true,
    isArchived: true,
  });
}

async function unarchiveOrder(id) {
  try {
    await api(`/api/orders/${id}/unarchive`, { method: "POST" });
    toast("Order restored to the dashboard.");
    loadArchive();
  } catch (e) {
    toast("Failed to unarchive: " + e.message, "error");
  }
}

async function loadArchive() {
  const orders = await api("/api/orders-archived");
  const grid = document.getElementById("archiveGrid");
  if (!orders.length) {
    grid.innerHTML = `<div class="text-center text-slate-400 py-12">No archived orders yet.</div>`;
    return;
  }
  grid.innerHTML = orders.map(archiveOrderCard).join("");
}
