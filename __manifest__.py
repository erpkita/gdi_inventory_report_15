# -*- coding: utf-8 -*-

{
    'name': 'GDI Inventory Report KIT',
    'version': '1.0',
    'description': 'Inventory Report KIT for GDI',
    'summary': 'List of Essential Inventory Report for Great Dynamic Indonesia',
    'author': 'veltics',
    'website': '',
    'license': 'LGPL-3',
    'category': 'inventory',
    'depends': [
        'gdi_erp_dev_v15'
    ],
    'data': [
        'security/ir.model.access.csv',
        'report/stock_card_report_template.xml',
        'wizard/view/stock_card_wizard_views.xml'
    ],
    'demo': [
    ],
    'auto_install': False,
    'application': False,
    'assets': {
        
    }
}