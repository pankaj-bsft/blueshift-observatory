// API Service for MBR Dashboard

const API_BASE_URL = 'http://localhost:8001';

const api = {
  async fetchData(fromDate, toDate) {
    try {
      const response = await fetch(`${API_BASE_URL}/api/fetch-data`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          from_date: fromDate,
          to_date: toDate,
        }),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to fetch data');
      }

      return await response.json();
    } catch (error) {
      console.error('API Error:', error);
      throw error;
    }
  },

  async exportExcel(fromDate, toDate) {
    try {
      const response = await fetch(`${API_BASE_URL}/api/export/excel`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          from_date: fromDate,
          to_date: toDate,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to export Excel');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mbr_deliverability_report_${fromDate}_to_${toDate}.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error('Export Excel Error:', error);
      throw error;
    }
  },

  async exportPDF(fromDate, toDate) {
    try {
      const response = await fetch(`${API_BASE_URL}/api/export/pdf`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          from_date: fromDate,
          to_date: toDate,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to export PDF');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `mbr_deliverability_report_${fromDate}_to_${toDate}.pdf`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (error) {
      console.error('Export PDF Error:', error);
      throw error;
    }
  },
};

window.api = api;
