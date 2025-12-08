# -*- coding: utf-8 -*-

from odoo import models, fields, api
from odoo.exceptions import UserError
from datetime import datetime


class StockCardWizard(models.TransientModel):
    _name = 'stock.card.wizard'
    _description = 'Stock Card Report Wizard'

    date_from = fields.Date(
        string='Date From',
        required=True,
        default=fields.Date.context_today
    )
    date_to = fields.Date(
        string='Date To',
        required=True,
        default=fields.Date.context_today
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        required=True,
        default=lambda self: self.env['stock.warehouse'].search([], limit=1)
    )
    brand_id = fields.Many2one(
        'product.brand',
        string='Product Brand',
        help='Filter products by brand. Leave empty for all brands.'
    )
    product_ids = fields.Many2many(
        'product.product',
        string='Products',
        help='Select specific products. Leave empty to include all products (or all products in selected brand).'
    )

    @api.onchange('brand_id')
    def _onchange_brand_id(self):
        """Clear product selection and update domain when brand changes"""
        self.product_ids = [(5, 0, 0)]  # Clear all products
        
        if self.brand_id:
            return {
                'domain': {
                    'product_ids': [('product_tmpl_id.brand_id', '=', self.brand_id.id)]
                }
            }
        else:
            return {
                'domain': {
                    'product_ids': []
                }
            }

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        """Validate date range"""
        for wizard in self:
            if wizard.date_from > wizard.date_to:
                raise UserError('Date From cannot be later than Date To.')



    def action_generate_report(self):
        """Generate and return the PDF report"""
        self.ensure_one()
        
        # Validate warehouse
        if not self.warehouse_id:
            raise UserError('Please select a warehouse.')
        
        # Prepare data for report
        data = {
            'wizard_id': self.id,
        }
        
        # Return report action
        return self.env.ref('gdi_inventory_report_15.action_report_stock_card').report_action(self, data=data)