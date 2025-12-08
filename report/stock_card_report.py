# -*- coding: utf-8 -*-

from odoo import api, models, fields, _
from odoo.exceptions import UserError
from datetime import datetime


class StockCardReport(models.AbstractModel):
    _name = 'report.gdi_inventory_report_15.report_stock_card_document'
    _description = 'Stock Card Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        """Main method called by Odoo to generate report"""
        if not data:
            raise UserError(_("Report data is missing, this report cannot be printed."))

        wizard_id = data.get('wizard_id')
        
        # Get wizard
        wizard = self.env['stock.card.wizard'].browse(wizard_id)
        
        if not wizard.exists():
            raise UserError(_("Wizard data not found."))
        
        # Prepare report data
        report_data = self._prepare_report_data(wizard)
        
        return {
            'doc_ids': docids,
            'doc_model': 'stock.card.wizard',
            'docs': wizard,
            'data': {
                'date_from': wizard.date_from,
                'date_to': wizard.date_to,
                'date_from_formatted': wizard.date_from.strftime('%d/%m/%Y'),
                'date_to_formatted': wizard.date_to.strftime('%d/%m/%Y'),
                'warehouse': wizard.warehouse_id.name,
                'brand': wizard.brand_id.name if wizard.brand_id else 'All Brands',
                'report_data': report_data,
            },
            'company': self.env.company,
        }

    def _prepare_report_data(self, wizard):
        """Prepare data for the report"""
        products = self._get_products(wizard)
        
        if not products:
            raise UserError(_('No products found with the selected criteria.'))
        
        location_id = wizard.warehouse_id.lot_stock_id.id
        
        # Get all child locations
        child_locations = self.env['stock.location'].search([
            ('id', 'child_of', location_id)
        ])
        
        report_data = []
        
        for product in products:
            opening_balance = self._get_opening_balance(
                product, location_id, wizard.date_from
            )
            
            moves = self._get_stock_moves(
                product, location_id, wizard.date_from, wizard.date_to
            )
            
            move_lines = []
            current_balance = opening_balance
            
            for move in moves:
                qty_in = 0
                qty_out = 0
                
                # Check if destination is in warehouse (including child locations)
                if move.location_dest_id.id in child_locations.ids:
                    # Incoming
                    qty_in = move.product_uom_qty
                    current_balance += qty_in
                
                # Check if source is in warehouse (including child locations)
                if move.location_id.id in child_locations.ids:
                    # Outgoing
                    qty_out = move.product_uom_qty
                    current_balance -= qty_out
                
                move_lines.append({
                    'date': move.date,
                    'product_name': move.product_id.display_name,
                    'reference': move.picking_id.name if move.picking_id else (move.reference or ''),
                    'doc_type': self._get_move_type(move),
                    'source': move.location_id.complete_name or move.location_id.name,
                    'destination': move.location_dest_id.complete_name or move.location_dest_id.name,
                    'qty_in': qty_in,
                    'qty_out': qty_out,
                    'balance': current_balance,
                })
            
            report_data.append({
                'product': product,
                'product_name': product.display_name,
                'product_code': product.item_code_ref or '',
                'uom': product.uom_id.name,
                'opening_balance': opening_balance,
                'closing_balance': current_balance,
                'moves': move_lines,
            })
        
        return report_data

    def _get_products(self, wizard):
        """Get products based on wizard selection"""
        if wizard.product_ids:
            # Use selected products
            return wizard.product_ids
        elif wizard.brand_id:
            # Get all products from selected brand
            return self.env['product.product'].search([
                ('product_tmpl_id.brand_id', '=', wizard.brand_id.id)
            ])
        else:
            # Get all products
            return self.env['product.product'].search([])

    def _get_opening_balance(self, product, location_id, date_from):
        """Calculate opening balance for a product at date_from"""
        # Get all child locations
        location = self.env['stock.location'].browse(location_id)
        child_locations = self.env['stock.location'].search([
            ('id', 'child_of', location_id)
        ])
        location_ids = child_locations.ids
        
        domain = [
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('date', '<', date_from)
        ]
        
        moves = self.env['stock.move'].search(domain)
        
        balance = 0
        for move in moves:
            if move.location_dest_id.id in location_ids:
                # Incoming to warehouse or its child locations
                balance += move.product_uom_qty
            if move.location_id.id in location_ids:
                # Outgoing from warehouse or its child locations
                balance -= move.product_uom_qty
        
        return balance

    def _get_stock_moves(self, product, location_id, date_from, date_to):
        """Get stock moves for a product within date range"""
        # Get all child locations
        location = self.env['stock.location'].browse(location_id)
        child_locations = self.env['stock.location'].search([
            ('id', 'child_of', location_id)
        ])
        location_ids = child_locations.ids
        
        domain = [
            ('product_id', '=', product.id),
            ('state', '=', 'done'),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
            '|',
            ('location_id', 'in', location_ids),
            ('location_dest_id', 'in', location_ids)
        ]
        
        return self.env['stock.move'].search(domain, order='date asc, id asc')

    def _get_move_type(self, move):
        """Determine document type from move"""
        if move.picking_id:
            picking_type = move.picking_id.picking_type_id
            if picking_type.code == 'incoming':
                return 'Receipt'
            elif picking_type.code == 'outgoing':
                return 'Delivery'
            elif picking_type.code == 'internal':
                return 'Internal Transfer'
        
        if move.picking_id or 'inventory' in (move.origin or '').lower():
            return 'Adjustment'
        
        return 'Movement'