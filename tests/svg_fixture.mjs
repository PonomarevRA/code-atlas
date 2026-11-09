/** Минимальный DOM сохраняет SVG identity, вложенность, классы и перенос существующих детей. */
export class Svg{
 constructor(tag){this.tag=tag;this.attributes={};this.children=[];this.listeners={};this.clientWidth=1100;this.clientHeight=800;this.classList={toggle:(name,on)=>{const classes=new Set(String(this.attributes.class||'').split(' ').filter(Boolean));if(on)classes.add(name);else classes.delete(name);this.attributes.class=[...classes].join(' ');},contains:name=>String(this.attributes.class||'').split(' ').includes(name)};}
 get firstChild(){return this.children[0];}
 get textContent(){return (this._textContent || '') + this.children.map(child=>child.textContent || '').join('');}
 set textContent(value){this._textContent=String(value);this.children=[];}
 setAttribute(name,value){this.attributes[name]=value;}
 append(...nodes){for(const node of nodes){if(node.parent)node.remove();node.parent=this;this.children.push(node);}}
 replaceChildren(...nodes){this.children=[];this.append(...nodes);}
 addEventListener(name,callback){this.listeners[name]=callback;}
 remove(){this.parent.children=this.parent.children.filter(node=>node!==this);}
 querySelector(selector){return this.querySelectorAll(selector)[0];}
 querySelectorAll(selector){const found=[];for(const node of this.children){const match=selector[0]==='#'?node.attributes.id===selector.slice(1):selector[0]==='.'?String(node.attributes.class||'').split(' ').includes(selector.slice(1)):node.tag===selector;if(match)found.push(node);found.push(...node.querySelectorAll(selector));}return found;}
}
