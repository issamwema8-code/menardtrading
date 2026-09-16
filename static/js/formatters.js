/**
 * Menard Trading CC — Central Client-Side Number & Currency Formatter
 * Provides canonical formatting functions for comma thousands separators across
 * Alpine.js components, dynamic line-item builders, tables, and Chart.js tooltips.
 */

(function(window) {
    'use strict';

    /**
     * Formats a numeric value with comma thousands separators and specified decimal places.
     * @param {number|string} val - Number to format
     * @param {number} [decimals=2] - Number of decimal places
     * @param {string} [defaultVal='0.00'] - Fallback for empty/null/invalid values
     * @returns {string} Formatted string e.g. "1,250,000.50"
     */
    function formatNumber(val, decimals = 2, defaultVal = '0.00') {
        if (val === null || val === undefined || val === '') {
            return defaultVal;
        }
        const cleanVal = typeof val === 'string' ? val.replace(/,/g, '').trim() : val;
        const num = parseFloat(cleanVal);
        if (isNaN(num)) {
            return defaultVal;
        }
        return num.toLocaleString('en-US', {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        });
    }

    /**
     * Formats a monetary value with a currency prefix and comma thousands separators.
     * @param {number|string} val - Monetary amount
     * @param {string} [currency='N$'] - Currency symbol (e.g. 'N$', 'R', 'KSh')
     * @param {number} [decimals=2] - Number of decimal places
     * @returns {string} Formatted monetary string e.g. "N$ 1,250,000.50" or "-N$ 500.00"
     */
    function formatMoney(val, currency = 'N$', decimals = 2) {
        const formatted = formatNumber(val, decimals);
        if (formatted.startsWith('-')) {
            return `-${currency} ${formatted.substring(1)}`;
        }
        return `${currency} ${formatted}`;
    }

    // Expose globally to window
    window.formatNumber = formatNumber;
    window.formatMoney = formatMoney;
    window.MenardFormatters = {
        formatNumber: formatNumber,
        formatMoney: formatMoney
    };

})(window);
